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

# Run each published library crate's tests in its own cargo
# resolution, as a crates.io consumer builds it. `make test` is one
# invocation over the workspace, and cargo unifies features across an
# invocation, so this is the only lane that sees a crate with the
# features it has by itself. Slower than `make test`, because each
# resolution rebuilds the dependencies whose features differ.
make test-crates

# Run linting (rustfmt + clippy)
make lint

# Run linting with auto-fix
make lint-fix

# Start a test QEMU SPICE server (UEFI latency guest, downloads on first run)
make test-qemu

# Start a full XFCE desktop guest instead (downloads ~770MB on first run)
make test-qemu-desktop

# Stop the test QEMU instance
make test-qemu-stop
```

`make build`, `make release` and `make test` first run `make fetch`
to populate the cargo cache over the network, then compile inside
the devcontainer with networking disabled and the cache mounted
read-only, so a dependency's `build.rs` cannot phone home at compile
time (see [build network isolation](/components/ryll/ci/#build-network-isolation)).
Run `make fetch` on its own to pre-download without building. After
adding or bumping a dependency in a `Cargo.toml`, run `make lock`
first: `fetch` is `--locked` and every compile is `--frozen`, so
`make lock` is the only target that will write `Cargo.lock`, and
without it the next build stops with "the lock file needs to be
updated". The cache lives in `.cargo-cache`;
override `CARGO_CACHE` to move it (for example to a location a CI
runner keeps between jobs). `make clean` removes the build output,
including the fuzz crate's separate `target/`, and removes the
cache only when it sits inside the checkout, so a shared
out-of-tree cache survives. Fuzzing corpora and crash artifacts
are kept.

## Building with a local Rust installation

If you have Rust installed locally with the required dependencies:

```bash
cargo build --release
```

**Required system dependencies** (Debian/Ubuntu):

```bash
apt-get install -y \
    build-essential \
    libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev libxcb1-dev \
    libx11-dev libxkbcommon-dev libgl1-mesa-dev libegl1-mesa-dev \
    libwayland-dev libssl-dev pkg-config
```

`build-essential` provides the C/C++ compiler that two vendored
codec crates need at build time: `openh264-sys2` (H.264, see
[Key dependencies](#key-dependencies) below) compiles Cisco's
openh264 C++ sources, and `mozjpeg-sys` compiles vendored
libjpeg-turbo (see
[spice-protocol.md](/components/ryll/spice-protocol/#image-types-and-compression)
for why that buys a no-runtime-dependency JPEG fallback). Neither
crate downloads a prebuilt binary. Both sit behind default-on
features of `shakenfist-spice-compression` (`encode` and
`h264-decode` for openh264, `mozjpeg` for mozjpeg-sys), and `ryll`
takes the renderer's default features, so every `ryll` build
compiles them and a C/C++ compiler is a build prerequisite. A
library consumer that leaves those features off does not build
them. A missing compiler
fails the build during their `cc`-crate compile step, not at link
time, so the error surfaces early with a clear "no C compiler
found"-style message. NASM (`nasm`) is not required for either
crate -- both fall back to a slower, non-SIMD codec path if it is
missing, so it's an optional install for anyone chasing codec
throughput, not a build prerequisite.

**macOS** (Apple Silicon): No additional system libraries are needed --
just Xcode Command Line Tools and Rust, which is also what provides
the C/C++ compiler for the same two crates. See
[development-macos.md](/components/ryll/development-macos/) for full setup
instructions.

## Cargo features

ryll ships several Cargo features, most of them default-on and
able to be opted out of at build time:

- **`gui`** (default-on) — eframe, egui, arboard, rfd, and the
  whole interactive UI.  Disabling it produces a `--headless`
  / `--web`-only binary that does not link the X11/Wayland/
  winit runtime and the runtime image drops libgl1, libx11-6,
  libxcb1, libxkbcommon0, libwayland-client0.  Running such a
  binary without `--headless` or `--web` exits with a clear
  "this binary was built without the `gui` feature" message.
- **`audio`** (default-on) — local audio output: cpal and rtrb
  in `shakenfist-spice-renderer`.  Disabling it drops libasound2
  from the runtime image.  The GUI and `--headless`, which play
  through the host's device, then have no local output and skip
  the SPICE playback channel at connect time (the rest of the
  session is unaffected).  `--web` hands the audio to a sink
  instead of a device, so it still links the playback channel
  (and its Opus decoder, which is always compiled) and the
  browser still gets audio.
- **`capture`** (default-on) — pcap-file + etherparse + mp4 for
  `--capture` recording.
- **`digest-decode`** (default-off) — adds the
  shakenfist-visual-digest crate as a git dependency and
  enables a polling task that scans the primary surface for a
  QR-encoded visual digest and emits a `digest_updated`
  control-socket event on each frame counter change.  Built
  only for the kerbside test harness; not in production ryll.
  `make lint` nonetheless builds ryll with this feature on, so
  the `#[cfg(feature = "digest-decode")]` arms are compiled in
  CI rather than only in the harness build -- a new
  `ChannelEvent` variant has to be cfg-correct in both
  configurations, because the event match in
  `ryll/src/app/events.rs` names every variant instead of
  ending in a wildcard.
- **`rdp`** (default-off) — the `--rdp` mode, which serves the
  SPICE session to stock RDP clients through the
  shakenfist-spice-rdp crate and IronRDP.  It is a heavy
  feature: IronRDP brings `sspi` and `picky` for CredSSP, its
  own copy of `reqwest`, separate from the one ryll uses, and
  `libz-sys`, which links the system zlib or compiles a bundled
  copy with the C compiler where pkg-config finds none.  Any
  build that enables it, and any workspace-wide build, moves
  `flate2` onto that C zlib backend.  `make lint` and `make
  test` build it; release builds leave it out.  Running a
  binary built without it with `--rdp` exits with a clear
  "this ryll binary was built without the `rdp` feature"
  message.

The slim test-harness binary is built with
`cargo build --release --no-default-features -p ryll`.  See
[control-socket-protocol.md](/components/ryll/control-socket-protocol/)
for the `surface_drawn` and `digest_updated` event shapes.

### Renderer features

`shakenfist-spice-renderer` has its own feature set so that a
consumer which only needs display decode, such as a server-role
frontend that relays the guest's screen, can leave out the C codecs
and device stacks.  All but `digest-decode` are default-on, so a plain
dependency still builds the full client:

| Feature | What it gates |
|---------|---------------|
| `audio` | Local audio output (cpal, rtrb).  The playback channel and Opus decode are always built; only `PlaybackOutput::Local` needs this |
| `usbredir` | The usbredir channel and the USB device backends (nusb) |
| `webdav` | The WebDAV folder-sharing channel and server |
| `encoder` | The live H.264 encoder (openh264, vendored C++) |
| `h264-decode` | H.264 stream decode; without it the display channel does not advertise H.264 |
| `mozjpeg` | SIMD JPEG decode (vendored libjpeg-turbo); without it JPEG falls back to the platform or pure-Rust decoder |
| `digest-decode` | Visual-digest QR detection (default-off) |

The slim renderer is built with
`cargo build -p shakenfist-spice-renderer --no-default-features`,
optionally naming the features needed.  `tools/check-slim-renderer.sh`
guards it, and `make lint` runs it: clippy with no default features,
clippy with each feature on its own (both with warnings denied), and a
check that the slim dependency tree contains none of the crates the
features gate.

## Workspace dependency convention

Every workspace crate carries `version.workspace = true`, so
the single version in the root `Cargo.toml` is the only place
a release bump has to happen.

Dependencies between workspace crates must declare **both** a
path and a version:

```toml
shakenfist-spice-renderer = { path = "../shakenfist-spice-renderer", version = "X.Y.Z" }
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

### Measuring idle CPU

`tools/measure-idle-cpu.sh <pid> [seconds]` reports where a running
ryll's CPU actually goes, per thread and per thread group, as a
percentage of one core. It samples `utime + stime` from
`/proc/<pid>/task/<tid>/stat` across a window, so it is Linux-only.

```
tools/measure-idle-cpu.sh "$(pgrep -f 'ryll --direct')" 60
```

Reach for it rather than `top` when the question is about CPU cost.
On a machine with no GPU the expensive threads are Mesa's `llvmpipe-*`
rasterisers, not ryll's own — a 6-core idle client once looked like a
43% main thread under `top` while 16 rasteriser threads carried the
rest. The per-group breakdown is what makes that visible.

Two readings are worth taking together. Idle, with the client
connected and untouched, should sit under 10% of one core; the same
measurement while the mouse moves over the surface should climb
sharply. A low idle number on its own does not distinguish "sleeps
correctly" from "stopped waking up", and only the second reading
tells them apart.

This is a manual diagnostic, not a test. The idle-wedge test below
runs headless, where there is no renderer and so no CPU cost of this
kind to see.

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

### What the K1 idle wedge was, and what it was not

The wedge took three days of dogfooding to pin down, mostly because
every visible symptom was downstream of the cause. What a later
investigation should not have to rediscover:

- **The cause was ryll's own session orchestrator.**
  `shakenfist-spice-renderer/src/session.rs` drained an intermediate
  `mpsc::channel(64)` only until `SessionInitialized` and
  `ChannelsAvailable` arrived, then dropped the receiver while the
  main channel kept emitting `ChannelEvent::Latency` on every PING.
  After about 65 pings (the T+466 s fingerprint, seen on every
  reproduction) the buffer filled and main blocked forever in
  `event_tx.send().await`. With main's whole `select!` suspended no
  PONGs went out. The fix replaced the temp channel with two
  `oneshot`s for the session id and channel list, so main sends into
  the real, drained `event_tx`. Every `event_tx.send()` in
  `main_channel.rs` also runs under a 5 s timeout
  (`MAIN_EVENT_SEND_TIMEOUT`), so a recurrence is a log line rather
  than a silent hang.
- **The server's 30 s "rcc has been unresponsive" log line is
  downstream.** `CLIENT_CONNECTIVITY_TIMEOUT` in spice-server's
  `main-channel-client.cpp` is genuinely 30 s and resets on any
  inbound byte, so it fires 30 s after main goes quiet. The client
  then notices the dead socket only when TCP keepalive gives up (30 s
  idle plus three probes at 15 s, 75 s), which is why the failing
  read turned up on an arbitrary channel, usually inputs, with
  "peer closed connection without sending TLS close_notify". The
  secondary channels' silence from T+300 s is the server pinging
  them only every 300 s, not a fault.
- **Ruled out, with the evidence.** A tokio, rustls, mio or kernel
  bug: the "missing waker" was the permit-available waker of an mpsc
  whose receiver was gone. macOS App Nap and screen lock: timing was
  identical with ryll in the foreground, in the background and on
  another virtual desktop, and a syslog search showed no lock events
  around the failures. A client keepalive timeout: every capture had
  `keepalive_timeout_fired: false`.
- **The mitigations never touched it.** The mirror keepalive, the
  inputs KEY_MODIFIERS keepalive and the spurious-PONG keepalive on
  main all kept *other* channels busy while main sat deadlocked. The
  KEY_MODIFIERS one was later removed outright, because it toggled
  the guest's locks (see the
  [channel diagnostics audit](/components/ryll/channel-diagnostics-audit/)).
- **Designed and deliberately not built.** A client-initiated PING on
  every channel that had been silent for 15 s, and a macOS App Nap
  opt-out (`NSProcessInfo beginActivity`). Both answered hypotheses the
  root cause disproved, and the opt-out was already unsupported by the
  foreground and background timing parity above. ryll therefore sends
  no application-level keepalive on any channel. If a long-idle
  disconnect reappears with `keepalive_timeout_fired` false and the
  main channel still answering PINGs, a per-channel PING is the
  first thing to try, and the disconnect report's per-channel
  `last_recv_ts_secs` is the evidence to read first.
- **The mirror timeout is 90 s, not 30 s,** so that the server's own
  30 s check fires first when the server is alive and leaves the more
  informative log line and FIN or RST in the pcap. It is a backstop for a
  server that vanished without either, not a liveness mechanism.

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
- **source-size-ratchet** - File-length ratchet, described below

## The function-length lint

`clippy::too_many_lines` is set to `warn` in the root `Cargo.toml`'s
`[workspace.lints.clippy]`, so every crate carrying
`[lints] workspace = true` inherits it, and every clippy lane in
`make lint` denies warnings. The threshold is **200 lines**, set as
`too-many-lines-threshold` in `clippy.toml`.

That figure was measured rather than chosen. Of about 4,145 functions
in the seven crates that inherit the workspace lints, 40 are over 100
lines, 21 are over 150 and 13 are over 200. Clippy's default of 100
would have asked for 40 exemptions, which is more than anybody reads;
200 asks for 13, which is an inventory a reviewer can take in at one
sitting. What the lint is for — stopping the next 600-line function
arriving unremarked — works at either figure, so the number was picked
on how readable its exception list is.

Those 13 functions each carry
`#[expect(clippy::too_many_lines, reason = "...")]`, and they are an
inventory rather than a backlog: turning the lint on asks for none of
them to be split. Each reason says why that particular function is
long and what splitting it would cost — a dispatch `match` over a wire
protocol whose arms share one channel's state, a decode loop whose
cursors every branch reads and writes, a mode entry point that is
order-bound setup. If one of them is shortened later its `#[expect]`
goes unfulfilled, which is a warning, which is a failed build, so the
exemption leaves with the length that justified it.

No function needed an `#[allow]`, and the reason is mechanical and
worth knowing before adding one. The lint counts the lines of the
function body's source span, not the code left after `cfg` stripping,
so a feature flag changes only *whether* a function is linted, never
its reported length. Every clippy lane that lints a given function
reports the same count, and where a function is compiled out its
`#[expect]` is compiled out with it. That is what makes `#[expect]`
safe here: an exemption cannot be satisfied in one of the eleven
clippy lanes `make lint` runs and unfulfilled in another, which was
the real risk, since `shakenfist-spice-renderer` holds the longest
functions in the workspace and is linted in ten of those lanes.

## The source size ratchet

`tools/source-size-ratchet.py` stops a source file that is already
over 1,500 lines from growing silently, and stops a new file crossing
that line. It never asks for a file to be split: a file listed in
`tools/source-size-baseline` may stay exactly as long as its entry for
ever. It counts every tracked file with a source extension (`.rs`,
`.py`, `.sh`, `.js`, `.css` and the like, wherever it lives), skipping
the trees listed in `tools/source-size-exclude`, each of which carries
a comment saying why it is not reviewed here. It runs as a pre-commit
hook and as the `source size ratchet` CI job (see [CI](/components/ryll/ci/)).

When it fails, the file in question either grew past its baseline
entry or is new to the list. The choices, in order of preference:

- Put the growth somewhere else — a new module along a seam that
  already exists, not a split at a line number.
- Or raise the entry (or add one, keeping the file sorted by path) by
  hand, and say in the pull request description why the file should
  stay whole and grow. The tool deliberately cannot do this edit for
  you; the one-line baseline diff is what makes the decision visible
  to a reviewer.

Never get under the line by deleting comments or docstrings that
explain why the code is the way it is. The tool counts lines and
cannot tell rationale from scaffolding, so a reviewer has to.

When a file shrinks, `python3 -I tools/source-size-ratchet.py
--update` lowers its entry, or drops it once the file is at or under
the threshold. `--update` only ever moves the baseline downwards.
`--init` rewrites the baseline from the current tree and is for
adoption only.

The script is vendored byte-for-byte from shakenfist/development's
`templates/source-size-ratchet/`, so one review of the template covers
every copy in the fleet. Change it there and re-copy it; never edit it
in place. Its tests live with the template, in that repository's
`scripts/tests/test_source_size_ratchet.py`, and that repository's
consistency audit is where a check that this copy is still current
belongs.

## Review tracking

Whole-file human review state (`REVIEWS.md`, `.vscode/*.weaudit*`)
is maintained with `tools/review-tracking.sh`, a wrapper around the
shared helper in the
[shakenfist/development](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md)
repository. In a clone it is run by hand, not from git hooks:
`prune` after a pull to discard reviews of files that have since
changed, `stamp` before committing new review marks, `regen` to
rebuild `REVIEWS.md`, `next` to pick an unreviewed file, `status`
to report effective coverage at HEAD, and `scope-orphans` to list
tracked files that are in neither `include` nor `exclude`, which
nobody has made a decision about. On develop itself the
`prune-reviews` workflow runs `prune` automatically after every
push, committing the result back as shakenfist-bot, and the daily
consistency audit in shakenfist/development files an issue when
five or more in-scope files need review.

`REVIEWS.md` is generated. Never edit it by hand: `stamp`, `prune`
and `regen` all rewrite it from the marks in `.vscode/*.weaudit`
and the stamped blob SHAs beside them, so a hand edit is discarded
by the next run of any of the three -- silently, because the file
that replaces it looks like a normal regeneration. Change the
review state and let the tooling render it.

## CI and automation

GitHub Actions CI builds and tests ryll on Linux (x86_64 + aarch64),
macOS (Apple Silicon), and Windows (x86_64 + aarch64) in two tiers. A
smoke tier runs on pull requests — lint, the self-hosted Linux x86_64
build and tests, a Windows cross-check, and the supply-chain scanners
— while a merge tier runs in the merge queue with the cross-platform
build matrix, so the expensive jobs run once, against the commit that
is about to land. The `cargo-fuzz` targets are in neither tier: they
build and smoke-run nightly from `fuzz.yml`, which files a GitHub
issue for each failing target rather than relying on anyone noticing
a red scheduled run. Linux x86_64 jobs run on self-hosted runners
with the build wrapped in the devcontainer (via the same Makefile
targets used locally); macOS, Windows, and aarch64 Linux use
GitHub-hosted runners because we own no matching hardware.
PRs also receive an automated code review via Claude Code. Changes
that only touch code-review artifacts (`REVIEWS.md`,
`.vscode/*.weaudit*`) skip every CI job and the CodeQL workflow. A
change to `.vscode/review-scope.toml` runs CI and gets the review
like code, since it decides what is reviewed at all; only CodeQL
skips it.

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

This is an inventory of what each dependency is *for*, not of which
version is in use. Versions are deliberately absent: the manifests are
the only place a version is true, a copy here goes stale at the next
bump, and a stale copy reads as a claim that the older version is still
supported. Run `cargo tree` or read the `Cargo.toml` that declares the
dependency.

- **eframe/egui** - Immediate mode GUI
- **tokio** - Async runtime
- **tokio-rustls** - TLS support
- **clap** - CLI parsing
- **rsa/sha1** - Authentication encryption
- **jpeg-decoder/mozjpeg** - JPEG decoding in
  `shakenfist-spice-compression`, as two of the backends that
  `best_for_platform()` in its `jpeg` module chooses between (ImageIO,
  WIC, VA-API, libjpeg-turbo via `mozjpeg`, then pure-Rust
  `jpeg-decoder`); see [the SPICE protocol notes](/components/ryll/spice-protocol/)
- **image** - PNG encoding of control-socket screenshots
- **cpal** - Cross-platform audio output
- **rtrb** - Lock-free ring buffer for audio sample passing
- **opus-decoder** - Pure-Rust Opus audio decoding
- **openh264** - H.264 decoding in `shakenfist-spice-compression`,
  and encoding behind its `encode` feature (with `openh264-sys2`
  for the options openh264 does not wrap), which the renderer's
  encoder pipeline and ryll's capture use.
- **jpeg-encoder** - JPEG encoding behind the `encode` feature of
  `shakenfist-spice-compression`
- **lz4_flex** - LZ4 image decode and encode in
  `shakenfist-spice-compression`, plus usbredir and webdav
  compression in `shakenfist-spice-renderer`
- **nusb** - USB device access (pure Rust, no libusb)
- **dav-server** - WebDAV server (RFC 4918, LocalFs backend)
- **hyper** - HTTP/1.1 framing for WebDAV byte-stream transport
- **webrtc** - DTLS/SRTP/ICE/SCTP/STUN stack for
  `shakenfist-spice-webrtc` (browser-bridge crate); an async
  shim over the sans-io `rtc` core, which is a direct
  dependency in its own right because webrtc's public API
  takes `rtc` types it does not re-export. `if-addrs`
  enumerates host interfaces to pick UDP bind addresses, and
  `async-trait` is required to implement
  `PeerConnectionEventHandler`. The version floor on `webrtc`
  and `rtc` is a correctness contract rather than a routine
  pin — the control datachannel is negotiated out of band, and
  only releases from the floor onwards honour that request.
  The floor and the reason for it are recorded together in
  `shakenfist-spice-webrtc/Cargo.toml`; do not lower it. See
  [web-mode-internals.md](/components/ryll/web-mode-internals/).
- **opus** - libopus bindings for the synthetic Opus
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

### Soaking `--web` and comparing against a baseline

`tools/web-soak.sh` samples a running `ryll --web` process while
driving the guest, and prints a summary in the same shape as the
0.17 baseline recorded in the "What `--web` costs, measured"
section of [web-mode-internals.md](/components/ryll/web-mode-internals/). Reach for
it when a change could plausibly affect memory or CPU over minutes
rather than seconds — the WebRTC write path especially, where the
integration tests only ever exercise a few seconds.

```bash
make test-qemu                       # guest, SPICE on 5900, QMP socket
ryll --web --direct localhost:5900 & # the process to sample
tools/web-soak.sh --pid $! --qmp /tmp/ryll-test-qemu-qmp.sock
```

Defaults are a 20-minute run sampled every 30 s, which is what the
baseline used; both are overridable. Per sample it records RSS,
per-thread CPU, whole-host CPU busy% and load average, to a CSV as
well as the terminal. The host figures are there because these soaks
usually run on a shared machine: contamination belongs in the data,
not folded into the result.

Two things it deliberately does not do. It does not start ryll or a
browser — an attended session is the point, and the numbers are only
comparable if the browser and its flags match whatever the baseline
used. And it does not read the pump drop counters or reaper events,
which ryll logs at `debug`; run ryll with `--verbose` and take them
out of the session log.

Note that `--verbose` turns on `debug` for the whole dependency tree,
not just ryll — webrtc-rs is talkative at that level, and that is
enough log to affect the numbers on a long run. Narrow it with
`RUST_LOG` instead, which ryll honours in place of `--verbose`'s
level: `RUST_LOG=info,shakenfist_spice_webrtc=debug,ryll=debug` gives
the drop counters and the reaper events without the rest of the tree,
so the counters can come from the session being measured. A
`RUST_LOG` that does not parse is reported at `warn!` and ignored,
and the run falls back to `--verbose`'s level.

If you are driving the uefi-latency-guest, note that any keypress
advances a fixed eight-colour cycle and one step in eight is black,
so the viewer legitimately goes black for one interval in eight. The
script says so at startup.

### Benchmarking GLZ dictionary references

`shakenfist-spice-compression` carries a manual benchmark for the cost
of cross-image GLZ references, which is what the dictionary's shared
source handling exists to keep down. It needs no guest and no server:

```sh
cargo run -p shakenfist-spice-compression --release \
    --no-default-features --features glz --example glz_references
```

It decodes a 1920x1080 image built from either one reference or 128
references into a cached source, checks the decoded pixels before
timing anything, and prints milliseconds per decode. Read it with
three caveats:

- It reports **elapsed decode time, not session frame rate**. A
  hundredfold change here is a change in one decode path, not in what
  a user sees: the figures measured in
  [shakenfist/ryll#739](https://github.com/shakenfist/ryll/pull/739)
  (175.7 ms to 1.5 ms at 128 references) are decode time alone.
- There is **no threshold and no CI job**. Nothing fails if it
  regresses, so it is only worth running deliberately, and only
  against another revision measured the same way — same command, same
  `--release` profile, same machine, nothing else loaded.
- It measures **one decoder against a one-entry dictionary**, so it
  says nothing about the other half of the old cost: the dictionary
  mutex, held across the copy, with several display channels sharing
  one dictionary. A change that helps there will not show up in these
  numbers at all.

## Manual verification against a desktop guest

Some behaviour cannot be tested from the automated suite at all,
because it needs a guest with a desktop session in it. `make
test-qemu-desktop` boots one: the shakenfist `debian-xfce:13` image
with `spice-vdagent`, an `intel-hda` audio device, user-mode
networking and a cloud-init seed ISO. XFCE autologins as `debian`
(password `ryll`), and the image ships with the screensaver and
lock screen disabled.

```bash
make test-qemu-desktop
target/release/ryll --web --direct localhost:5900
```

Each run starts from a fresh qcow2 overlay, so the downloaded base
image stays pristine and repeated runs begin from identical state.
`make test-qemu-stop` stops it.

The devices in `tools/start-desktop-qemu.sh` are the point of the
target rather than incidental, and leaving any of them out produces
a symptom that looks like a client bug:

| Device | What it makes testable | Symptom without it |
|---|---|---|
| vdagent virtserialport | Client (absolute) mouse mode, viewport resize | Server mouse mode: absolute pointer messages are ignored and the guest pointer does not move |
| `intel-hda` + `hda-duplex` | The SPICE playback channel | Silence, indistinguishable from a broken audio path |
| user-mode networking | cloud-init, `apt` in the guest | cloud-init waits out its datasource search on every boot |

The image ships **no audio player** — no `alsa-utils`,
`pulseaudio-utils` or libcanberra — so there is nothing in it that
can make a sound, and the audio check below cannot be done until you
add one:

```bash
sudo apt-get install -y alsa-utils      # in the guest
speaker-test -t sine -f 440 -l 3
```

The guest has user-mode networking, so that works out of the box.
Worth knowing before you conclude the audio path is broken: silence
with no player installed looks exactly like silence with a broken
playback channel.

Worth checking in ryll's own log when verifying `--web`:

- `main: mouse mode=N (...)` — `2` is client mode, which is what a
  guest running vdagent should negotiate. `1` means server mode, and
  everything about the pointer will behave differently.
- `playback: MODE: 3` — Opus. Mode `1` is raw PCM, which web mode
  does not yet transcode, so audio is silent by design.
- `web: encoder restarted at WxH@30fps` — the encode resolution. If
  it does not match the guest's surface, the browser is watching a
  scaled image.
- `webrtc: no H.264 payload type negotiated` — the browser offered no
  H.264, so there is no video at all. See below.

### A browser with no H.264 gets no video

ryll's web mode encodes H.264 and nothing else, so a browser that does
not offer H.264 connects successfully and gets no picture. Audio,
input, cursor and viewport resize all keep working, which is what
makes the symptom look like a rendering bug rather than a negotiation
one.

The browser is told: the page shows a panel over the video area
explaining that this browser offered no H.264 and pointing at the
OpenH264 plugin. The server logs `no H.264 payload type negotiated`
once, stops the encoder for that session, and parks the video pump —
so a session in this state costs no CPU and produces no further log
output. Before that fix (issues #289 and #290) the only signal was a
black rectangle, and the log filled with one
`Failed to send RTP: unsupported codec type by this transceiver` per
packet at the frame rate.

The notice is sent in reply to the browser's `hello`, not pushed when
negotiation settles: negotiation finishes inside `accept_offer`,
before SCTP has opened the control datachannel, and anything written
then is dropped. This is the same pull the mouse mode uses, for the
same reason.

Firefox is the browser this happens on. It ships H.264 for WebRTC via
Cisco's OpenH264 plugin, and if that plugin has not loaded, Firefox
still lists H.264 in `RTCRtpReceiver.getCapabilities('video')` while
omitting it from the offer — so the capability list is not evidence.
`tools/browser-offer-probe.py` is the check that follows from that. It
serves a page to a browser, prints the video codecs from the offer the
page actually generated with `getCapabilities` beside them, and exits
non-zero when the two disagree:

```bash
tools/browser-offer-probe.py --browser chromium
tools/browser-offer-probe.py --browser firefox-esr --profile ~/.mozilla/firefox/<profile>
```

Pass a real `--profile` for Firefox. Without one it uses a throwaway
profile, which has never downloaded OpenH264 and would report no H.264
for a reason that has nothing to do with the browser you meant to
test.

Chromium carries H.264 in-tree and does not have this failure mode,
which makes it the browser to reach for when deciding whether a
problem is ryll's — and the control to run the probe against when
deciding whether the probe itself is telling the truth.

### Verifying `--rdp`

`--rdp` needs a binary built with the `rdp` feature. The Makefile's
`lint` and `test` targets build it, but `make build` and `make release`
do not, so run `make -n build` to see the docker line and add
`--features rdp` to its cargo command; that leaves the binary at
`target/debug/ryll` (or `target/release/ryll` from the `release`
target's line). Then start the desktop guest and point ryll at it:

```bash
make test-qemu-desktop
target/debug/ryll --rdp --direct localhost:5900
```

ryll prints the address, port, username, password and certificate
fingerprint. If another RDP server already holds 3389 it falls back to an
ephemeral port and prints that one. Connect with FreeRDP (3.15.0 from
Debian 13's `freerdp3-x11` works):

```bash
xfreerdp3 /v:127.0.0.1:PORT /u:ryll /p:PASSWORD /cert:tofu
```

Use `/sec:nla` against a server started with `--rdp-security nla`. Check
that typing, left, right and middle buttons, and the wheel all reach the
guest. To measure what the extra hop costs, run `tools/rdp-measure.sh
--pid PID --port PORT --seconds 30` against the ryll process while the
guest is idle and during each of the three workloads: typing in a
terminal, scrolling a long page and playing a video. It reports CPU and
bytes sent. Video is expensive
because the client receives RemoteFX bitmaps of a region that changes
about 28 times a second.

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
