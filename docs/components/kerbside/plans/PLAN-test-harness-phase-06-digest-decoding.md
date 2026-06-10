# Phase 6: Ryll Cargo feature work + digest decoding + restore keypress-to-screen latency

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). The bulk of
this phase lives in the ryll repo; the kerbside follow-ups land
on the existing `test-harness` branch. Per the master plan's
single-home rule, the plan file lives here in
`docs/plans/`.

## Goal

Three concerns bundled because they all touch ryll's Cargo
features, channel-event handling, and control-socket protocol:

1. **Restore keypress-to-screen latency semantics.** Phase 4
   regressed the loadtest from a true user-perceivable metric
   (keypress to next visible draw) to SPICE PING/PONG round-
   trip latency because v1.0 of the control socket had no
   "a draw just happened" event. Add a `surface_drawn`
   control-socket event so phase 4's orchestrator can compute
   `latency = surface_drawn_time - send_key_time` and write
   that to the CSV instead. This is the user-facing reason
   phase 6 exists; the other two concerns are supporting.
2. **Add a `digest-decode` Cargo feature to ryll.** Off by
   default. When enabled, ryll pulls in
   `shakenfist-visual-digest` (phase 1's crate) and exposes a
   `digest_updated` control-socket event that fires when a new
   QR-encoded digest is detected on the primary surface. This
   is the spine phase 7 needs for its Sextant scenario test.
3. **Split ryll's GUI/audio code behind Cargo features so a
   slim headless binary is buildable.** Today
   `cargo build --no-default-features -p ryll` (which kerbside's
   loadtest Dockerfile and the phase 5 CI workflow both call)
   still drags in eframe, egui, egui-winit, arboard, cpal,
   and opus-decoder unconditionally. Phase 6 introduces `gui`
   and `audio` features (default-on) so `--no-default-features`
   produces a binary that doesn't link the GUI/audio stack
   at all. The kerbside loadtest and CI lane runtime images
   then drop libgl1 / libx11-6 / libxcb1 / libxkbcommon0 /
   libwayland-client0 / libasound2.

After this phase: phase 4's loadtest measures the metric the
operator originally wanted, phase 5's CI lane builds a
noticeably slimmer ryll binary, and phase 7 has a working
digest-event channel to assert against.

This phase is scope-bounded to:

- Cargo feature refactoring in ryll for `gui` and `audio`
  (default-on) and `digest-decode` (default-off).
- A `surface_drawn` wire event added in a v1.0 → v1.1 protocol
  bump.
- A `digest_updated` wire event added behind the
  `digest-decode` feature in the same v1.1 bump.
- One kerbside commit that switches the loadtest orchestrator
  back to keypress-to-screen latency, slims the loadtest
  Dockerfile, and slims the phase 5 CI workflow.
- Doc and master-plan-status touchups.

Out of scope for phase 6:

- A new control-socket verb. Existing verbs (`hello`, `status`,
  `screenshot`, `subscribe`, `unsubscribe`, `send_key`, `paste`)
  are sufficient. The digest_updated event is **push**, not
  pull-via-verb.
- Sextant scenario assertions. Phase 7 owns those; phase 6
  just lights up the event stream phase 7 will consume.
- Mouse, USB, vdagent clipboard, audio, or WebDAV scenarios.
- Cross-platform validation of the new features. Phase 6
  verifies on Linux only (the only platform the test harness
  targets). Windows and macOS feature-combination CI is
  deferred.
- Publishing a release ryll binary. Phase 4's Future-work
  entry covers this; phase 6 does not.
- Restoring the keypress-to-screen metric for any consumer
  *other* than phase 4's orchestrator. There are no other
  consumers today.
- A new branch on kerbside. The phase 6 kerbside commit lands
  on the existing `test-harness` branch.

## Decisions baked into this plan

These are judgment calls made while drafting, surfaced
explicitly so they can be challenged before code lands.

- **Features are named `gui`, `audio`, and `digest-decode`,
  not `headless`.** Idiomatic Cargo features are additive: a
  `headless` pseudo-feature would have to act as a negation
  ("no GUI"), which violates that convention and breaks
  `cargo build --all-features`. Instead, `gui` and `audio`
  are default-on, and `cargo build --no-default-features`
  (which kerbside already invokes) yields the slim binary.
  The master plan and phase 4 plan both referred to a
  "headless" feature by name; both will be updated in step 6e
  to reflect the actual naming. The user-facing effect is
  identical to the planned-named "headless" — same build
  command, same slim binary, same dropped runtime libs.
- **`surface_drawn` is emitted by the control-server event
  translator, not as a new `ChannelEvent` variant.** The
  display channel already publishes `ImageReady`,
  `ImageReadyChroma`, `ImageReadyAlpha`, `FillRect`,
  `CopyBits`, and `Invert` to the broadcast bus. The
  translator at `shakenfist-spice-renderer/src/control/server.rs`
  currently returns `None` for all of these (control/server.rs
  around line 585). Phase 6 extends `translate_event` to map
  each of those six variants to a single `Event { event:
  "surface_drawn", data: { display_channel_id, surface_id,
  produced_at_secs, wallclock_us } }`. No new `ChannelEvent`
  variant; no new plumbing in `surface_mirror.rs` or
  `session.rs`; no observer trait on the mirror. Cheapest
  viable change.
- **`surface_drawn` fires on every draw command, not on
  `DisplayMark`.** Reasoning: keypress-to-screen latency is
  defined as "time between key press and the next pixel
  change visible to the user". The first draw command after
  a key press *is* the first visible change. `DisplayMark` is
  a server-driven frame boundary that headless SPICE servers
  do not emit reliably; relying on it would make the metric
  flaky in CI. The orchestrator deduplicates by taking the
  first `surface_drawn` after each `send_key down` and
  ignoring subsequent events from the same logical frame.
- **`surface_drawn` carries `produced_at_secs` AND `wallclock_us`.**
  `produced_at_secs` is the renderer's monotonic timestamp
  (already populated on every draw event in
  `channels/mod.rs`); `wallclock_us` is the same wallclock
  field the `latency` event already carries. The orchestrator
  uses `wallclock_us` to compute the keypress-to-screen delta
  because the orchestrator records keypress times in
  wallclock too; cross-clock arithmetic is the wrong move.
- **The `digest_updated` event is push, not pull.** When the
  `digest-decode` feature is enabled, ryll polls the primary
  surface mirror after every batch of draws, runs the QR
  decoder, and emits `digest_updated` if a new digest payload
  has been detected (deduplicated by frame counter). No
  client-side `decode_digest` verb. This keeps clients simple
  (subscribe and receive) and gives phase 7 a stream of
  digest events to assert sequences against.
- **`digest_updated`'s payload carries the parsed digest, not
  the raw bytes.** The wire shape is `{ event:
  "digest_updated", data: { frame_counter, framebuffer_hash,
  events: [{kind, payload}, ...], wallclock_us } }`. Clients
  do not need to know the QR wire format. Trade-off:
  schema drift in
  `shakenfist-visual-digest` propagates to the control-socket
  contract. Acceptable for now (single client; both repos in
  lockstep). Documented as a "may revisit if the digest crate
  ships v2.0".
- **Visual-digest is consumed as a git dependency, not a path
  dependency.** `ryll/Cargo.toml` adds
  `shakenfist-visual-digest = { git = "https://github.com/shakenfist/visual-digest-rust.git",
  features = ["qr"], optional = true }`. Developers who want
  to iterate on visual-digest locally can use
  `[patch.crates-io]` or `[patch."https://..."]` in their
  per-clone `.cargo/config.toml`. Reasons: (a) avoids a
  hard-coded relative-path assumption that breaks for anyone
  not laying out the repos exactly the same way the operator
  does; (b) makes ryll's CI matrix self-contained; (c)
  matches how phase 1's digest crate is consumed by Sextant
  (also git, not path).
- **No protocol-version downgrade fallback in the
  orchestrator.** When the orchestrator subscribes to
  `surface_drawn`, the hello response must include
  `surface_drawn` in `supported_events` or the orchestrator
  exits non-zero. Soft falling back to PING/PONG would mask
  the very regression phase 6 exists to fix. CI runs a fresh
  ryll built from main, so this is never a real-world
  problem; making it a hard fail keeps developers honest if
  they accidentally point the orchestrator at a stale ryll
  during local debugging.
- **Bump protocol v1.0 → v1.1 as a single change containing
  both `surface_drawn` and `digest_updated`.** The two events
  are related (both observability surfaces growing) and
  arriving together avoids two consecutive minor bumps.
  Clients hello'ing with `protocol_version: "1.0"` still
  work — `supported_events` is the negotiation surface, not
  the major-version compare. The hello-time mismatch check
  fires only on major-version differences.
- **Verify on Linux only.** The CI matrix in `ryll/.github/workflows/ci.yml`
  builds on macOS and Windows too. Phase 6's feature
  additions should not break those builds (the Linux-only
  test we care about is "does the slim binary run") but if
  they do, the implementing agent fixes the breakage; we
  don't add a new feature-combination matrix dimension. Cross-
  platform feature testing is a follow-up.

## Situation

### Ryll today (relevant pieces)

- Workspace at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/ryll`.
  Members: `ryll` (binary), `shakenfist-spice-compression`,
  `shakenfist-spice-protocol`, `shakenfist-spice-renderer`,
  `shakenfist-spice-usbredir`, `shakenfist-spice-webrtc`.
- `ryll/Cargo.toml` features:
  `default = ["capture"]`, `capture = ["dep:pcap-file",
  "dep:etherparse", "dep:mp4"]`, `tokio-console`. No GUI or
  audio feature today; eframe, egui, egui-winit, arboard,
  cpal, opus-decoder are direct unconditional deps.
- `shakenfist-spice-renderer/Cargo.toml` has no `[features]`
  section. cpal, opus-decoder, image, base64, openh264 are
  unconditional deps.
- `shakenfist-spice-renderer/src/session.rs` — the `run_headless`
  entry point and `HeadlessStatus` (an `Arc<AtomicBool>` plus
  `Arc<tokio::sync::Mutex<SurfaceMirror>>`).
- `shakenfist-spice-renderer/src/surface_mirror.rs` — passive
  pixel store; `apply_event(&mut self, event: &ChannelEvent)`
  is the single mutation entry point. No "changed" notification.
- `shakenfist-spice-renderer/src/channels/display.rs` — emits
  `ChannelEvent::ImageReady` / `ImageReadyChroma` / `ImageReadyAlpha`
  / `FillRect` / `CopyBits` / `Invert` for draws,
  `DisplayMark` for frame boundaries.
- `shakenfist-spice-renderer/src/channels/mod.rs` — the
  `ChannelEvent` enum. No digest variant.
- `shakenfist-spice-renderer/src/control/server.rs` —
  `event_translator_task` and `translate_event`. Currently
  returns `None` for all display events (around line 585).
- `shakenfist-spice-renderer/src/control/protocol.rs` —
  protocol-version constant `"1.0"`. `SUPPORTED_EVENTS` is
  the array `["latency", "agent_connected", "paste_completed",
  "paste_failed", "dropped"]`.
- `docs/control-socket-protocol.md` — v1.0 spec. Scope
  section says `digest_updated` is reserved for a v1.1 bump
  "added as a new event name without changing any existing
  envelope or verb".
- Phase 3 merged to ryll's `develop` on 2026-06-08. The v1.0
  control socket is now reachable from any clone of ryll
  main; phase 6 branches off `develop` directly.

### `shakenfist-visual-digest` (phase 1 output)

- Workspace at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/visual-digest-rust`.
  Members: `shakenfist-visual-digest` (library), `digest-decode`
  (CLI).
- Library features: `default = []`, `decode = ["dep:thiserror"]`,
  `qr = ["dep:rqrr", "dep:image", "decode"]`, `cli =
  ["decode", "qr", "serde"]`. Phase 6 enables `qr`.
- Decoder API: `pub fn decode(bytes: &[u8]) -> Result<Digest,
  DecodeError>` returns the parsed `Digest { frame_counter,
  channel_hashes, raw_records, unknown_records, framebuffer_hash }`.
- QR API: `pub fn decode_qr_rgba(rgba: &[u8], width: u32,
  height: u32) -> Option<Vec<u8>>` returns the byte-mode
  payload suitable for `decode()`.
- Published: `publish = false`. Consumed via git dependency
  by ryll in phase 6.
- Phase 1 PR is pending operator review on the Sextant
  consumer side; the crate itself is on `main` in the
  visual-digest-rust repo.

### Kerbside today (relevant pieces)

- `loadtests/latency/orchestrator.py` subscribes to `latency`
  and writes `sample_ms / 1000.0` (seconds) per line to the
  CSV. Module docstring at lines 9–13 flags the metric as
  temporary.
- `loadtests/latency/Dockerfile` stage 1 line 44:
  `cargo build --release --no-default-features -p ryll`.
  Stage 2 runtime deps include libasound2, libgl1, libx11-6,
  libxcb1, libxkbcommon0, libwayland-client0.
- `.github/workflows/direct-qemu-functional.yml` line 52:
  same cargo invocation. Steps 28–34 install the same GUI/
  audio runtime libs plus build-time `-dev` packages.
- Phase 4 plan's Future-work entries name "Restore keypress-
  to-screen latency semantics (committed for phase 6+)" and
  "Shrink the loadtest image via a ryll `headless` Cargo
  feature (committed for phase 6)" — both resolved here.
- Phase 5 success criteria don't depend on phase 6; the slim
  image is a quality improvement, not a correctness fix.

## Mission and problem statement

After phase 6:

- `ryll/ryll/Cargo.toml` declares features `gui = [...]`,
  `audio = [...]`, `digest-decode = [...]`, with
  `default = ["gui", "audio", "capture"]`. `cargo build
  --no-default-features -p ryll` produces a binary whose
  `cargo tree --no-default-features -p ryll` output contains
  no eframe, no egui*, no arboard, no cpal, no opus-decoder.
- Every GUI / audio import in the ryll binary crate's source
  (and any in the renderer crate, if applicable) is gated
  behind `#[cfg(feature = "gui")]` or `#[cfg(feature = "audio")]`.
  `cargo build --no-default-features -p ryll`, `cargo build
  --no-default-features --features gui -p ryll`, `cargo
  build --no-default-features --features audio -p ryll`,
  `cargo build --all-features -p ryll`, and the default
  `cargo build -p ryll` all succeed on Linux.
- Behavioural change: running the `--no-default-features`
  binary with no flags exits with a clear "this binary has no
  GUI; pass --headless" error (no eframe-required-by-runtime
  crash). Running with `--headless --control-socket /tmp/x`
  works identically to today.
- `ryll/shakenfist-spice-renderer/src/control/protocol.rs`:
  `PROTOCOL_VERSION = "1.1"`. `SUPPORTED_EVENTS` adds
  `"surface_drawn"` unconditionally and `"digest_updated"`
  behind `#[cfg(feature = "digest-decode")]`. Hello handshake
  continues to accept `"1.0"` from clients.
- `ryll/shakenfist-spice-renderer/src/control/server.rs`:
  `translate_event` maps each of `ImageReady`,
  `ImageReadyChroma`, `ImageReadyAlpha`, `FillRect`,
  `CopyBits`, `Invert` to a `surface_drawn` wire event with
  `{ display_channel_id, surface_id, produced_at_secs,
  wallclock_us }`.
- `digest-decode` feature: a small polling task in the
  headless session, gated behind the feature, runs after
  every batch of broadcast events (or on a short interval),
  reads the primary surface RGBA, calls
  `shakenfist_visual_digest::qr::decode_qr_rgba` followed by
  `shakenfist_visual_digest::decode`, and emits
  `digest_updated` if the new `frame_counter` differs from
  the last one observed. Failure modes (no QR found, decode
  error) are logged at debug level and not surfaced as events.
- `docs/control-socket-protocol.md` updated: protocol version
  1.1, `surface_drawn` and `digest_updated` documented (the
  latter explicitly tagged as "available only when ryll is
  built with `--features digest-decode`").
- Ryll integration tests at `shakenfist-spice-renderer/tests/control_socket.rs`
  gain coverage for `surface_drawn` emission on synthetic
  draw events and (behind cfg) `digest_updated` emission from
  a known-good QR fixture lifted from `shakenfist-visual-digest`'s
  test fixtures.
- `kerbside/loadtests/latency/orchestrator.py` subscribes to
  `latency`, `dropped`, AND `surface_drawn`. Cadence thread
  records keypress wallclock when sending `send_key` down.
  Main thread computes `latency = surface_drawn.wallclock_us
  - keypress_wallclock_us` for the first `surface_drawn`
  after each keypress; CSV column is that delta in seconds.
  The orchestrator hard-fails at startup if the hello
  response doesn't advertise `surface_drawn`.
- `kerbside/loadtests/latency/Dockerfile` stage 2 drops
  libasound2, libgl1, libx11-6, libxcb1, libxkbcommon0,
  libwayland-client0 from the runtime apt-get list. Stage 1
  build deps stay (the cargo build needs them at compile
  time; only the runtime image shrinks).
- `kerbside/.github/workflows/direct-qemu-functional.yml`
  drops the same runtime libs. Build-time `-dev` packages
  stay for the cargo step.
- `README.md`, `AGENTS.md`, and `ARCHITECTURE.md` in ryll
  document the new features and event. Kerbside's loadtest
  README is updated to reflect the metric flip back to
  keypress-to-screen.
- Master plan phase 6 row marked "Implementation complete;
  PR pending operator". Phase 4 plan's Future-work entries
  about keypress-to-screen and the headless feature are
  updated to point at the phase 6 commits that resolved them.
- `pre-commit run --all-files` clean on every kerbside commit;
  `cargo fmt --check` and `cargo clippy --all-targets
  --all-features` clean on every ryll commit.

## Open questions

These do not block writing this plan but must be resolved
before or during implementation:

- **Does eframe's own dependency graph include any runtime
  shared libs that survive removing the eframe Cargo dep?**
  The plan assumes that gating the eframe import behind
  `#[cfg(feature = "gui")]` removes the runtime dep too.
  Verify with `cargo tree --no-default-features -p ryll`
  during step 6a. If it doesn't (some transitive crate pulls
  in eframe-style runtime libs unconditionally), step 6a
  documents the residual deps and the kerbside Dockerfile
  shrinks by less than the full list.
- **Should `digest_updated` debounce or rate-limit?** A
  Sextant guest that's actively painting will update the
  digest's frame counter every few draws. The
  control-socket broadcast bus has a 256-slot bound; a
  fast-firing event could overflow it for slow consumers.
  Default plan: emit unconditionally; document the rate-
  limit question in the protocol spec. If it turns out to be
  a problem for phase 7, add a `--digest-min-interval-ms`
  CLI flag later.
- **Where does the digest-decoding polling task sit?** In
  `run_headless` directly, in a new `digest.rs` module under
  the renderer crate, or in a wrapper module under the
  binary crate? Default: a new submodule
  `shakenfist-spice-renderer/src/digest.rs` gated behind the
  `digest-decode` feature, with the task spawned from
  `run_headless` (also under cfg). Confirmable during step 6c.
- **Does ryll's existing CI (`ci.yml`) need a new matrix
  dimension to cover the feature combinations?** Out of
  scope per the "verify on Linux only" decision. Step 6a
  notes any CI red as something for a follow-up.

## Execution

Each step is one logical change → one commit. Ryll commits
land on a new ryll branch `test-harness-phase-6`, branched
from ryll's `develop` (the phase 3 PR merged on 2026-06-08,
so the v1.0 control socket is now reachable from develop;
no need to branch off the now-deleted phase 3 branch).
Kerbside commits land on the existing `test-harness` branch.
Per the master plan, ryll and kerbside changes do NOT share
git operations.

| Step | Repo | Effort | Model | Isolation | Brief for sub-agent |
|------|------|--------|-------|-----------|---------------------|
| 6a. ryll: introduce `gui` + `audio` features | ryll | high | opus | none | Create a new branch `test-harness-phase-6` from ryll's `develop` (the phase 3 PR has merged) and verify with `git status`. In `ryll/ryll/Cargo.toml`, move `eframe`, `egui-winit`, `arboard` into `[dependencies]` blocks with `optional = true`, and `cpal`, `opus-decoder` similarly. Add `[features]` entries: `default = ["gui", "audio", "capture"]`, `gui = ["dep:eframe", "dep:egui-winit", "dep:arboard"]`, `audio = ["dep:cpal", "dep:opus-decoder"]`, `capture = ...` (unchanged), `tokio-console = ...` (unchanged). Walk every `use eframe::*`, every `use egui::*`, every `use arboard::*`, every `use cpal::*`, every `use opus_decoder::*` in `ryll/src/**`; gate them behind `#[cfg(feature = "gui")]` or `#[cfg(feature = "audio")]` as appropriate. Where a function or struct uses one of those crates, gate the whole function/struct. Where a `main.rs` branch uses GUI code, gate the branch and add a clear error message for the no-feature case (e.g., `eprintln!("ryll built without the `gui` feature; pass --headless")` and exit non-zero). Inspect `shakenfist-spice-renderer` for cpal/opus imports too — that crate may also need feature gating. If so, mirror the feature additions in its Cargo.toml. Verify: (1) `cargo build -p ryll` (default) succeeds. (2) `cargo build --no-default-features -p ryll` succeeds. (3) `cargo build --no-default-features --features gui -p ryll` succeeds. (4) `cargo build --no-default-features --features audio -p ryll` succeeds. (5) `cargo build --all-features -p ryll` succeeds. (6) `cargo tree --no-default-features -p ryll \| grep -E '^(eframe\|egui\|arboard\|cpal\|opus)' \|\| true` is empty. (7) `cargo clippy --all-targets --no-default-features -p ryll -- -D warnings` clean. (8) `cargo clippy --all-targets --all-features -p ryll -- -D warnings` clean. (9) `cargo fmt --check` clean. Record `cargo tree --no-default-features -p ryll` and `cargo tree -p ryll` (default) output sizes in the commit body. If a transitive dep brings in GUI runtime libs even with `--no-default-features`, document it in the commit body — the kerbside Dockerfile shrink in step 6d will need to know. **Do not** add any v1.1 protocol changes in this commit; that's step 6b. **Do not** touch the control-socket files in this commit. One commit. |
| 6b. ryll: bump protocol to v1.1 with `surface_drawn` | ryll | medium | opus | none | Same `test-harness-phase-6` branch. In `shakenfist-spice-renderer/src/control/protocol.rs`: change `PROTOCOL_VERSION` from `"1.0"` to `"1.1"`. Add `"surface_drawn"` to `SUPPORTED_EVENTS`. Add a `SurfaceDrawnData { display_channel_id: u8, surface_id: u32, produced_at_secs: f64, wallclock_us: u64 }` struct or equivalent. Update the hello-handshake compatibility logic: clients sending `protocol_version: "1.0"` still receive a normal hello response (the v1.1 server is backwards-compatible for v1.0 clients); clients sending `"1.1"` get the same response. Major-version mismatches still error out. In `shakenfist-spice-renderer/src/control/server.rs::translate_event`: replace the current `None` arm for the six draw variants (`ImageReady`, `ImageReadyChroma`, `ImageReadyAlpha`, `FillRect`, `CopyBits`, `Invert`) with a single helper that constructs a `surface_drawn` wire event carrying `display_channel_id`, `surface_id`, `produced_at_secs` (already on every draw variant), and a freshly-computed `wallclock_us` (mirror what the `latency` arm does at lines 500–519 — same `SystemTime::now()` pattern). Update `docs/control-socket-protocol.md`: bump the protocol version, add a `surface_drawn` section documenting the wire shape, the firing rule ("on every draw command that modifies a display surface"), the back-pressure note (default 256-slot per-client buffer; consumers must drain or risk being dropped), and the recommended consumer pattern ("subscribers wanting per-keypress latency should take the first event after each keypress"). Update `protocol.rs`'s `SUPPORTED_METHODS` if any helper text references protocol version. Extend `shakenfist-spice-renderer/tests/control_socket.rs` with a `surface_drawn_emitted_on_draw_event` test: spawn the server, subscribe to `surface_drawn`, inject a synthetic `ChannelEvent::ImageReady` via the broadcast sender, assert the client receives a `surface_drawn` wire event with the expected fields. Add a `surface_drawn_emitted_for_each_draw_variant` test that exercises all six draw variants. Verify: `cargo test -p shakenfist-spice-renderer control_socket` clean. `cargo build -p ryll` (default) clean. `cargo build --no-default-features -p ryll` clean. `cargo fmt --check` + `cargo clippy --all-targets -- -D warnings` clean. **Do not** add the digest-decode plumbing in this commit; that's 6c. One commit. |
| 6c. ryll: `digest-decode` feature + `digest_updated` event | ryll | high | opus | none | Same branch. In `ryll/ryll/Cargo.toml`: add `shakenfist-visual-digest = { git = "https://github.com/shakenfist/visual-digest-rust.git", features = ["qr"], optional = true }`. Add a `digest-decode = ["dep:shakenfist-visual-digest"]` feature. Mirror in `shakenfist-spice-renderer/Cargo.toml` if the decode call lives in that crate (which is the recommended layout). Create `shakenfist-spice-renderer/src/digest.rs` (whole file gated `#![cfg(feature = "digest-decode")]`): a small async task that takes an `Arc<tokio::sync::Mutex<SurfaceMirror>>` plus the broadcast sender, polls the primary surface RGBA at a sensible interval (default 100ms; tunable later), calls `shakenfist_visual_digest::qr::decode_qr_rgba(rgba, width, height)`, if `Some(bytes)` then `shakenfist_visual_digest::decode(&bytes)`, and on success compares the `frame_counter` to a remembered last value; on change, emits a new `ChannelEvent::DigestUpdated { frame_counter, framebuffer_hash, events, wallclock_us }` variant (which you'll add behind cfg in `channels/mod.rs`). Wire the task into `run_headless` behind `#[cfg(feature = "digest-decode")]`. In `protocol.rs`: gate `"digest_updated"` in `SUPPORTED_EVENTS` behind `#[cfg(feature = "digest-decode")]`. In `server.rs::translate_event`: add a cfg'd arm for `ChannelEvent::DigestUpdated` that emits a `digest_updated` wire event whose `data` carries `frame_counter`, `framebuffer_hash`, `events` (as an array of `{kind, payload}` objects — translate the `Record` variants to a JSON-friendly form; look at the digest crate's `Display` impl or its CLI tool's pretty-printer for a model), and `wallclock_us`. Update `docs/control-socket-protocol.md`: add a `digest_updated` section with the explicit note "available only when ryll is built with `--features digest-decode`; otherwise this event is not advertised in the hello response and subscribe-by-name returns an empty list". Add a control_socket test that verifies digest_updated emission behind `#[cfg(feature = "digest-decode")]`: lift a known-good QR fixture from `shakenfist-visual-digest`'s test suite (a PNG with a synthesized digest in the bottom-right), inject a synthetic `ChannelEvent::ImageReady` whose pixel buffer is the fixture's RGBA, run the digest task once, assert a `digest_updated` wire event with the expected `frame_counter`. Verify: (1) `cargo build -p ryll` (default; digest-decode off) clean. (2) `cargo build --features digest-decode -p ryll` clean. (3) `cargo build --all-features -p ryll` clean. (4) `cargo test -p shakenfist-spice-renderer --features digest-decode control_socket` clean. (5) `cargo clippy --all-features --all-targets -- -D warnings` clean. (6) `cargo fmt --check` clean. One commit. |
| 6d. kerbside: orchestrator switch-back + slim images | kerbside | medium | sonnet | none | On the kerbside `test-harness` branch. Three coupled changes in ONE commit (they're all flowing from the same v1.1 + headless landing): (1) `loadtests/latency/orchestrator.py`: subscribe to `["latency", "dropped", "surface_drawn"]`. At startup, parse the hello response's `supported_events` and exit non-zero with a clear stderr message if `surface_drawn` is absent. Modify the cadence thread to record `keypress_wallclock_us` (use `time.time() * 1_000_000` rounded to int) immediately before issuing `send_key down`; push it into a thread-safe deque shared with the main thread. In the main reader, on each `surface_drawn` event, pop the oldest pending keypress timestamp if any (FIFO); compute `latency_seconds = (surface_drawn.wallclock_us - keypress_wallclock_us) / 1_000_000.0`; append to CSV. Drop the latency-from-PING/PONG path — the CSV column is now keypress-to-screen as it was before phase 4. Update the module docstring: remove the "Phase 6 will restore" sentence; replace with "This script reports keypress-to-screen latency via SPICE PING/PONG... no wait — via the `surface_drawn` control-socket event introduced in phase 6 / protocol v1.1." Be precise. (2) `loadtests/latency/Dockerfile`: stage 2 apt-get `install -y` list — drop `libasound2`, `libgl1`, `libx11-6`, `libxcb1`, `libxkbcommon0`, `libwayland-client0`. Stage 1 build-time `-dev` packages stay. Add a one-line comment at the top of stage 2 referencing phase 6 as the reason these are gone now. Document the new image size in the commit body. (3) `.github/workflows/direct-qemu-functional.yml`: drop the same runtime libs from the "Install system packages" step. Build-time `-dev` packages stay. Verification: `python3 -m py_compile loadtests/latency/orchestrator.py` clean. `pre-commit run --all-files` clean. `actionlint .github/workflows/direct-qemu-functional.yml` clean. Do NOT run the Docker build or the workflow; the operator will. One commit. |
| 6e. Docs + master plan status | mixed | low | sonnet | none | Two commits: one on the ryll `test-harness-phase-6` branch updating ryll's `README.md`, `AGENTS.md`, `ARCHITECTURE.md` to mention the new features and the digest event flow. One on the kerbside `test-harness` branch updating the loadtest's `README.md` section to reflect the keypress-to-screen metric (drop the "temporary regression" wording), update `kerbside/docs/plans/PLAN-test-harness.md` phase 6 row status to "Implementation complete; PR pending operator", and update `kerbside/docs/plans/PLAN-test-harness-phase-04-port-latency.md`'s Future-work entries to point at the resolving phase 6 commits by hash. Run `pre-commit run --all-files` clean before each commit. |

### Sequencing notes

- 6a lands first because it changes the Cargo file structure
  every subsequent step depends on. The feature gate
  refactoring is also the most likely to surface unexpected
  cross-platform CI breakage; landing it alone makes that
  isolatable.
- 6b lands second. It's a pure protocol surface addition and
  does not touch the digest-decode plumbing.
- 6c lands third because it depends on 6b's v1.1 protocol
  scaffolding being in place.
- 6d lands on the kerbside branch after 6a–6c are visible on
  ryll's `test-harness-phase-6` branch (because the
  orchestrator's hello assertion needs a v1.1 server to talk
  to during any local testing). Kerbside and ryll branches
  do not need to ship in the same PR cadence — the
  orchestrator works against any future ryll release
  carrying v1.1.
- 6e lands last, on both branches separately.
- The operator opens the ryll PR for `test-harness-phase-6`
  and the (already-pending) kerbside PR for `test-harness`
  whenever the matching CI is green. Phase 7 cannot start
  until both PRs merge and ryll's v1.1 binary is reachable
  from a `git clone --depth 1 https://github.com/shakenfist/ryll.git`.

### Branch and PR shape

- **ryll**: New branch `test-harness-phase-6` branched from
  ryll's `develop`. Phase 3 merged on 2026-06-08, so the
  v1.0 control socket is on develop and phase 6 can layer
  v1.1 + digest-decode + the `gui`/`audio` feature split on
  top directly. Steps 6a, 6b, 6c, and the ryll half of 6e
  land here. Five commits total.
- **kerbside**: Existing `test-harness` branch. Step 6d and
  the kerbside half of 6e land there. Two commits total.
- The kerbside PR continues to accumulate phase 1–6
  kerbside-side commits. Whether to split the kerbside PR
  by phase is a master-plan-level decision, not phase 6's.

## Agent guidance

This phase plan follows the conventions in
`PLAN-TEMPLATE.md` at the kerbside repo root. The execution
model, effort levels, model-choice guidance, brief-writing
standards, and management-session review checklist all apply
unchanged and are not duplicated here.

Notes specific to phase 6:

- **Cargo feature refactoring is fiddly across a large
  codebase.** Step 6a's sub-agent should expect to do a lot
  of file reads, find every GUI/audio import, and gate
  conservatively. A single missed `use eframe::App;` will
  break `--no-default-features`. The verification matrix in
  the brief is the safety net.
- **The protocol bump is backwards-compatible.** Old v1.0
  clients hello'ing with `"1.0"` still work; new clients
  hello with `"1.1"`. Server logic should accept both. Do
  not error on v1.0 just because the server is v1.1.
- **`digest_updated` lives behind a feature flag end-to-end.**
  No production ryll binary carries the digest decoder. The
  `digest-decode` feature is for the test harness only. If
  step 6c's sub-agent is tempted to enable it by default
  "because it's useful", push back.
- **Don't over-engineer the digest polling task.** A simple
  100ms tokio interval + a `last_frame_counter: Option<u32>`
  on the task is sufficient. No debouncing, no rate-limit
  knobs, no per-surface deduplication beyond the frame
  counter compare. Phase 7 will tell us if any of those are
  needed; right now they're speculative complexity.
- **The orchestrator's hard-fail on missing `surface_drawn`
  is intentional.** Resist any sub-agent suggestion to add a
  "fall back to PING/PONG" code path. The whole point of
  phase 6 is to never produce a PING/PONG CSV again.
- **Master plan and phase 4 plan reference a "headless"
  feature by name.** Step 6e fixes those references to point
  at the actual `gui` / `audio` feature names. Don't try to
  add a `headless` feature in step 6a just to match the prose
  — the refactor in step 6e is the right place.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the step and how
the work you intend to do aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 6 is done when:

- On the ryll `test-harness-phase-6` branch:
  - `cargo build -p ryll` (default features) succeeds.
  - `cargo build --no-default-features -p ryll` succeeds and
    `cargo tree --no-default-features -p ryll` contains no
    eframe, egui*, arboard, cpal, opus-decoder.
  - `cargo build --all-features -p ryll` succeeds.
  - `cargo test -p shakenfist-spice-renderer --all-features
    control_socket` succeeds.
  - `cargo fmt --check` and `cargo clippy --all-targets
    --all-features -- -D warnings` clean.
  - `docs/control-socket-protocol.md` reports protocol
    version 1.1 with `surface_drawn` and `digest_updated`
    sections.
  - `README.md`, `AGENTS.md`, `ARCHITECTURE.md` updated.
- On the kerbside `test-harness` branch:
  - `loadtests/latency/orchestrator.py` subscribes to
    `surface_drawn` and writes keypress-to-screen latency
    samples to the CSV.
  - `loadtests/latency/Dockerfile` and
    `.github/workflows/direct-qemu-functional.yml` drop the
    GUI/audio runtime libs.
  - `pre-commit run --all-files` clean.
  - `actionlint .github/workflows/direct-qemu-functional.yml`
    clean.
  - The master plan's phase 6 row is marked
    "Implementation complete; PR pending operator".
  - Phase 4 plan's Future-work entries for keypress-to-screen
    and the headless feature are updated with phase 6 commit
    references.

### Future work

Items deliberately deferred from phase 6:

- **Cross-platform feature CI.** Adding a matrix dimension
  to ryll's CI that covers `{macOS, Windows} × {default,
  --no-default-features, --features digest-decode}`. Phase 6
  verifies Linux only.
- **Rate-limit knobs for `digest_updated`.** If phase 7
  finds the event fires too frequently, add a
  `--digest-min-interval-ms` flag.
- **A `decode_digest` request verb** that lets a client
  request a one-shot digest decode on demand. Today's
  push-only model is sufficient for phase 7.
- **A `surface_drawn` event filtered by surface ID.**
  Clients today get every draw on every surface; if phase 7
  needs to ignore overlay surfaces, add subscribe-parameter
  filtering then.
- **Publishing `shakenfist-visual-digest` to crates.io.**
  Phase 6 consumes it via git; a published release would
  let ryll pin a version.
- **Removing the GUI/audio code paths entirely from ryll.**
  Feature gates are the conservative choice for now; if the
  GUI never gets used in practice, deleting it is a
  follow-up.

### Bugs fixed during this work

(None yet.)
