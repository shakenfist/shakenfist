# Phase 2: Encoder pipeline (framebuffer → H.264 NAL units)

## Prompt

Before responding to questions or making changes, explore the
codebase. Read the master plan at
`docs/plans/PLAN-web-frontend.md` (especially Resolutions §2
encoder choice and §4 frame pacing) and the Phase 1 plan and
its execution history. Key files for this phase:

- `ryll/src/capture.rs` — the closest in-tree precedent for an
  openh264 pipeline. The `VideoWriter` struct (~line 175) shows
  the lifecycle: `Encoder::new()`, `RgbaSliceU8` wrapper,
  `YUVBuffer::from_rgb_source()` for RGBA→YUV conversion,
  `force_intra_frame()` for keyframe-on-demand,
  `encoder.encode(&yuv)` returning a layered bitstream, and
  the NAL-extraction loop (types 7=SPS, 8=PPS, 5=IDR slice,
  else=non-IDR slice).
- `shakenfist-spice-renderer/src/display/surface.rs` — owns
  RGBA pixels, `pixels(&self) -> &[u8]`, `size(&self) -> (u32, u32)`,
  `is_dirty(&self) -> bool`, `consume_dirty(&mut self) -> bool`
  (read-and-clear).
- `shakenfist-spice-renderer/src/session.rs` — the existing
  orchestrator that the `--web` mode will eventually layer
  with its own `EncoderTask` driver (Phase 4–5). Phase 2 does
  not wire production usage; we only build the encoder in
  isolation.
- `ryll/Cargo.toml` — `openh264 = { version = "0.6", optional = true }`
  is already gated behind the `capture` feature. Phase 2 will
  add the dep to the renderer's `Cargo.toml` (unconditional
  for now per master plan; the "ship one fat binary" stance
  defers feature-gating to future work).

External references: openh264 0.6 docs, the H.264 NAL unit
type table (7=SPS, 8=PPS, 5=IDR, 1=non-IDR), `webrtc-rs`'s
`H264Payloader` (in `webrtc-rtp`) for the Phase 3 pre-flight.

Flag any uncertainty rather than guessing.

## Goal

Build a live H.264 encoder driven by framebuffer mutation,
ready to be consumed by the WebRTC plumbing in Phase 3. After
this phase:

- `shakenfist-spice-renderer/src/encoder/` is a new module
  containing a `FrameSource` trait, an `H264Encoder` stateful
  encoder, an `EncoderTask` async driver (30 fps cap,
  encode-on-dirty, keyframe-on-demand), and supporting types
  (`EncodedFrame`, `EncoderControl`).
- A test harness produces synthetic frames via a simple
  `FrameSource` impl and the encoder writes the resulting
  Annex-B NAL stream to a `.h264` file that can be played
  with `ffplay test.h264`.
- A small unit test confirms `webrtc-rs`'s H.264 packetiser
  accepts our NAL output without errors. This is the master
  plan's required pre-flight before Phase 3 takes a hard
  dependency on H.264.

No network code. No `--web` mode wiring. No GUI changes.

## Scope

In:

- A new `encoder` module inside the renderer crate.
- Adding `openh264 = "0.6"` and `webrtc-rtp` (or the parent
  `webrtc` crate, scoped to just the rtp packetiser) to the
  renderer's `Cargo.toml`.
- A `FrameSource` trait that decouples the encoder from how
  pixels arrive. Phase 4–5 will provide a production impl;
  Phase 2 ships only a synthetic test impl.
- Stateful H.264 encoder with explicit keyframe-on-demand.
- 30 fps cap + encode-on-dirty + force-keyframe-on-attach
  pacing in an async tokio task.
- Annex-B NAL-unit output framing. (Annex-B prepends each NAL
  with the start code `00 00 00 01`; this is what
  `webrtc-rs`'s H.264 packetiser expects, and what `ffplay`
  accepts as a raw `.h264` file.)
- Test harness that produces a `.h264` file consumable by
  `ffplay`.
- Unit test against `webrtc-rs::rtp::codecs::h264::H264Payloader`.

Out:

- Production wiring (the encoder receiving pixels from real
  SPICE display channel events). That belongs in Phase 4–5
  when the `--web` orchestrator exists.
- VP8 swap. The pre-flight in step 2e is *just a test* —
  if it fails, we add VP8 in a follow-up commit, but Phase 2
  ships H.264-only by default.
- Per-rectangle dirty tracking. Master plan defers to future
  work.
- Hardware encoders (NVENC/QSV/VAAPI). Future work.
- Cargo feature gating. Master plan defers; the encoder is
  unconditionally compiled in MVP.
- Bitrate / rate-control tuning beyond openh264 defaults.
  We can revisit if the Phase 5 CPU-budget instrumentation
  surfaces a problem.
- Re-removing the `capture` feature gate from the existing
  `--capture` flow. The two paths can coexist; consolidation
  is a separate follow-up.

## Approach

### Module shape

`shakenfist-spice-renderer/src/encoder/`:

- `mod.rs` — re-exports the public API.
- `frame_source.rs` — the `FrameSource` trait + a
  `SyntheticFrameSource` test impl.
- `h264.rs` — the `H264Encoder` (stateful wrapper around
  openh264), RGBA → YUV conversion, NAL-unit extraction in
  Annex-B framing.
- `task.rs` — the `EncoderTask` async driver with the 30 fps
  / dirty / keyframe loop.

The renderer's `lib.rs` re-exports:

```rust
pub mod encoder;
pub use encoder::{
    EncodedFrame, EncoderControl, EncoderTask, FrameSource,
    H264Encoder,
};
```

### Frame source abstraction

```rust
pub trait FrameSource: Send + 'static {
    /// Acquire the next frame to encode. Returns None if no
    /// new frame is available since the last call. The
    /// implementation handles dirty tracking and any
    /// synchronisation with concurrent writers; the encoder
    /// just consumes.
    ///
    /// The returned reference is valid until the next call
    /// to `next_frame`. Implementations may copy pixels into
    /// an internal staging buffer to satisfy this lifetime.
    fn next_frame(&mut self) -> Option<FrameRef<'_>>;
}

pub struct FrameRef<'a> {
    pub width: u32,
    pub height: u32,
    pub rgba: &'a [u8],
    /// Wall-clock timestamp in microseconds. Used by the
    /// task to derive RTP timestamps later in Phase 3. Pick
    /// a stable origin (e.g. encoder start instant) and
    /// monotonically increase.
    pub timestamp_us: u64,
}
```

Production usage in Phase 4–5 will provide a `FrameSource`
that owns/observes the renderer's surface map. Phase 2 only
ships `SyntheticFrameSource` for testing — animated test
pattern, e.g. a moving gradient over a fixed background.

### H.264 encoder (stateful)

```rust
pub struct H264Encoder {
    inner: openh264::encoder::Encoder,
    width: u32,
    height: u32,
}

impl H264Encoder {
    pub fn new(width: u32, height: u32) -> Result<Self> { ... }

    /// Encode one RGBA frame, returning Annex-B framed NAL
    /// units. If `force_keyframe` is true, this frame is
    /// emitted as an IDR (which produces SPS/PPS NALs too).
    pub fn encode(
        &mut self,
        rgba: &[u8],
        force_keyframe: bool,
    ) -> Result<EncodedFrame> { ... }
}

pub struct EncodedFrame {
    /// Annex-B-framed NAL units. Each NAL is prefixed with
    /// the 4-byte start code `00 00 00 01`. Concatenating
    /// `EncodedFrame::nal_units` over time produces a valid
    /// `.h264` Annex-B stream.
    pub nal_units: Vec<Vec<u8>>,
    pub timestamp_us: u64,
    pub keyframe: bool,
}
```

Key constraint inherited from openh264 / `capture.rs`: even
dimensions required (`w & !1`, `h & !1`). If the surface
size is odd, round down and crop.

### Async task driver

```rust
pub struct EncoderTask<S: FrameSource> {
    encoder: H264Encoder,
    source: S,
    output: mpsc::Sender<EncodedFrame>,
    control: mpsc::Receiver<EncoderControl>,
    fps_cap: u32,
}

pub enum EncoderControl {
    /// Force the next frame to be an IDR keyframe. Called
    /// by Phase 3+ whenever a new viewer attaches.
    RequestKeyframe,
    /// Stop the task.
    Stop,
}

impl<S: FrameSource> EncoderTask<S> {
    pub fn spawn(...) -> tokio::task::JoinHandle<Result<()>> {
        tokio::task::spawn_blocking(|| {
            // openh264 is a sync C library; run on a blocking
            // thread to avoid stalling the tokio executor on
            // long encodes.
            ...
        })
    }
}
```

The task loop selects on:

1. `control_rx.recv()` — handle `RequestKeyframe` (set a
   `keyframe_pending` flag) and `Stop` (break).
2. Tick at `1_000_000 / fps_cap` microsecond cadence (33333
   us at 30 fps). On tick, call `source.next_frame()`. If
   `Some`, encode (forcing keyframe if `keyframe_pending`)
   and send the `EncodedFrame` on `output`. If `None`, skip
   the tick — no idle frames.

The `spawn_blocking` choice trades async fairness for
encoder simplicity: openh264 is pure CPU; running it inside
the async executor would block other tasks. A blocking
thread is the right model. The select! loop runs inside
that thread using `tokio::runtime::Handle::current()` to
poll the channels — or, simpler, the task uses
`std::thread::spawn` + `blocking_recv` / `blocking_send` on
crossbeam-style channels and reserves tokio for the rest.
Implementation detail; the agent picks whichever is cleanest.

### VP8 contingency pre-flight (step 2e)

The master plan says: "Before declaring Phase 2 done, run a
small integration test through `webrtc-rs`'s H.264
packetiser to confirm browser playback works." We can
approximate "browser playback works" with a unit test that:

1. Creates a `webrtc::rtp::codecs::h264::H264Payloader`.
2. Feeds it the NAL bytes from a few `EncodedFrame`s
   produced by the encoder (with the Annex-B start codes
   stripped, since the payloader expects raw NALs).
3. Asserts: the payloader succeeds, produces non-empty RTP
   payloads, and the first packet of an IDR contains
   payload type indicating SPS/PPS/IDR aggregation per
   RFC 6184.

If that succeeds, Phase 2 is done with H.264. If it fails
(packetiser rejects the NALs, missing SPS/PPS handling,
etc.), the contingency is a single follow-up commit
introducing a VP8 alternative via `vpx-encode`. Same
`FrameSource` and `EncoderTask` shape; different inner
encoder. Don't pre-build the VP8 path; only build it if
2e fails.

## Prerequisites

- Phase 1 complete. (It is.)
- thought-bubble rebased onto current develop. (It is.)

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a   | low    | sonnet | none | Scaffold the encoder module. Create `shakenfist-spice-renderer/src/encoder/{mod,frame_source,h264,task}.rs` (the latter three as empty stubs with TODO-style placeholders). Define the public types in `mod.rs` (re-exports) and the `FrameSource` / `FrameRef` types in `frame_source.rs`. Add `openh264 = "0.6"` to `shakenfist-spice-renderer/Cargo.toml`. Add `pub mod encoder;` and the re-exports to the renderer's `lib.rs`. Run `make build`, `make lint`, `make test`, `pre-commit run --all-files`. No new logic yet — this commit just establishes the module shape. Single commit. |
| 2b   | high   | opus  | worktree | Implement `H264Encoder` in `h264.rs`. Lift the openh264 setup from `ryll/src/capture.rs:175-310` (read it carefully): `Encoder::new()`, `RgbaSliceU8::new`, `YUVBuffer::from_rgb_source`, `force_intra_frame()`, `encode(&yuv)`, the NAL-extraction loop with type-7 (SPS), type-8 (PPS), type-5 (IDR), type-1 (non-IDR) handling. Differences from `capture.rs`: (1) no MP4 muxing — emit Annex-B framed NAL units directly (each NAL prefixed with `[0x00, 0x00, 0x00, 0x01]`); (2) keyframe-on-demand via a `force_keyframe: bool` parameter, not just on first frame; (3) no `last_timestamp_ms` state — that lives in the task. The struct holds `(width, height, openh264 Encoder)`. Even-dimension constraint: `w & !1`, `h & !1`; round down + return `Err` if either becomes 0. Add a unit test that encodes a 64x64 black frame, asserts the result has at least one IDR + SPS + PPS, and asserts subsequent encodes (without `force_keyframe`) produce smaller bitstreams. Run `make` checks. Worktree because the openh264 lifecycle has subtle failure modes (encoder state corruption on bad inputs) and we want to be able to throw the worktree away if the experiment goes sideways. Single commit. |
| 2c   | medium | sonnet | none | Implement `EncoderTask` and `EncoderControl` in `task.rs`. The task takes a `FrameSource`, an `H264Encoder`, an `mpsc::Sender<EncodedFrame>`, an `mpsc::Receiver<EncoderControl>`, and an `fps_cap: u32`. Loop: select on the control receiver and a timer tick at `1_000_000 / fps_cap` us. On `RequestKeyframe`, set a `keyframe_pending` flag for the next frame. On `Stop`, break. On tick, `source.next_frame()` — if `Some(frame)`, encode (forcing keyframe if pending), send `EncodedFrame` on output, clear the flag; if `None`, skip. Use `tokio::task::spawn_blocking` because openh264 is a sync C library and blocking the tokio executor on encode times is bad. Inside the blocking thread, channels are `tokio::sync::mpsc` accessed via `blocking_recv` / `blocking_send` (or `try_recv` if you prefer non-blocking polling). Returns `JoinHandle<Result<()>>`. Add a unit test that spawns the task with a `SyntheticFrameSource` (defined in 2d, but stub-feed it here for the test if 2d is not yet landed — or better: have 2d add the test atop 2c's groundwork). Run `make` checks. Single commit. |
| 2d   | medium | sonnet | none | Build the test harness. Add a `SyntheticFrameSource` to `frame_source.rs` that generates a 1280x720 RGBA frame with an animated gradient (e.g. a moving horizontal sine band over a fixed checkerboard); the timestamp is the frame index × 33333 us. Add an integration-style test in `shakenfist-spice-renderer/tests/encoder_smoke.rs` that: spawns an `EncoderTask` with the synthetic source and a 30 fps cap; runs it for ~3 seconds (90 frames); collects the output `EncodedFrame`s; concatenates the Annex-B-framed NALs; writes the result to `target/encoder_smoke.h264`. Test asserts: at least one keyframe, total frame count ≈ 90 (allow ±5 for tick scheduling), file is non-empty. The test does not assert decode correctness — that's a manual `ffplay target/encoder_smoke.h264` check after the test runs. Mention that in the test's doc comment so the human verifying the phase knows. Run `make` checks. Single commit. |
| 2e   | high   | opus  | worktree | The webrtc-rs H.264 packetiser pre-flight. Add `webrtc-rtp = "0.x"` (or `webrtc = "0.x"` scoped) as a renderer dev-dependency. Add a unit test in `shakenfist-spice-renderer/tests/webrtc_h264_smoke.rs` that: encodes ~3 frames with the H264Encoder (one IDR + two non-IDR); for each `EncodedFrame`, strips the Annex-B start codes from each NAL and feeds the raw NALs to `webrtc::rtp::codecs::h264::H264Payloader::payload(mtu, &nal)` (MTU ~1200); asserts the payloader returns `Ok(non_empty_payloads)` and that for the IDR frame, payloads include the SPS and PPS NAL types as STAP-A or single-NAL-unit packets (per RFC 6184). If the test passes: Phase 2 is done. If it fails or hits API surprises (e.g. payloader expects a different framing): commit the test as-is documenting the failure, then a follow-up commit adds VP8 via `vpx-encode` as the encoder backend. Worktree because this is the highest-risk pre-flight: if the test surprises us, the whole branch may need reshaping and we want to be able to throw the worktree away. Single commit (or two if VP8 contingency triggers). |

After 2e, the renderer crate's `src/encoder/` has `mod.rs`,
`frame_source.rs`, `h264.rs`, `task.rs`. The renderer's
`Cargo.toml` has `openh264` as a regular dep and
`webrtc-rtp` (or scoped `webrtc`) as a dev-dep. Tests in
`shakenfist-spice-renderer/tests/` cover the smoke run +
the packetiser pre-flight.

## Step details

### Step 2b expanded brief

The lift from `capture.rs` is mostly mechanical, but pay
attention to:

1. **Annex-B framing.** `capture.rs` builds an MP4-shaped
   `frame_data` Vec by appending NAL bodies *without* start
   codes (because MP4 uses length-prefix framing). Phase 2
   needs Annex-B framing: each NAL prefixed with
   `[0x00, 0x00, 0x00, 0x01]`. Build the output as
   `Vec<Vec<u8>>` where each inner `Vec` is one Annex-B
   framed NAL. The caller can concatenate for `.h264` file
   output or strip start codes for RTP payloading.

2. **SPS/PPS handling.** Each IDR encode emits SPS (NAL type
   7) and PPS (NAL type 8) followed by the IDR slice (type
   5). Non-IDR encodes emit only the slice (type 1). Emit
   *all* NALs from each `encode()` call; don't filter SPS/PPS
   to first frame only — the receiver may need them after a
   forced keyframe.

3. **Keyframe-on-demand.** openh264's `force_intra_frame()`
   is sticky — it sets a flag that the next encode consumes.
   Call it before the next `encode()` whenever
   `force_keyframe == true`. Test that calling it twice
   between encodes still produces only one IDR (don't
   double-force).

4. **Encoder error recovery.** If `encode()` returns `Err`,
   the encoder may be in a bad state. Strategy: log the
   error, return it from the public `encode()` method, and
   let the caller decide whether to recreate the encoder.
   Don't try to recover internally.

5. **No `Mp4Writer` / `mp4::AvcConfig`.** Drop all the MP4
   muxing code entirely — it does not belong in the live
   path.

### Step 2c expanded brief

The async runtime model has two reasonable shapes:

- **`spawn_blocking`** — the encoder loop runs on tokio's
  blocking pool. Channels are `tokio::sync::mpsc` accessed
  via `blocking_recv()`. Simple, plays well with tokio.
- **`std::thread::spawn`** — fully detached from tokio.
  Channels are `crossbeam::channel`. More independent but
  adds a dep.

Pick `spawn_blocking`. It's already idiomatic in this
codebase (see how the audio path uses cpal, which is
similar shape).

The 30 fps tick can be a `std::thread::sleep` inside the
blocking thread — a tokio timer would require entering the
async context which is awkward inside `spawn_blocking`. A
simple `Instant::now()` + `Duration::from_micros(33_333) -
elapsed` sleep handles drift well enough. If frame encode
takes longer than the budget, skip the next tick (don't
catch up, just move on).

The keyframe-pending flag is local to the loop — no need
for atomic / mutex since the loop is single-threaded.

### Step 2e expanded brief

The test must speak Annex-B → raw NAL conversion: strip
the start codes before handing NALs to the payloader. In
pseudo-code:

```rust
for nal_with_start_code in encoded_frame.nal_units {
    // Strip the leading 4-byte start code [0,0,0,1].
    let raw_nal = &nal_with_start_code[4..];
    let payloads = payloader.payload(1200, &raw_nal.into())?;
    assert!(!payloads.is_empty());
}
```

If the payloader API differs (the H264Payloader takes
`Bytes` or has a different contract), adapt — the goal is
"the packetiser doesn't reject our NAL output". If it
rejects, that's the signal to switch to VP8. Document the
exact failure mode in the commit message before adding the
VP8 alternative.

If the test passes: this is a small, fast unit test that
should live in the codebase as a regression check. It does
not boot a full WebRTC connection — that's Phase 3.

## Acceptance criteria

- `make lint` passes after each of 2a, 2b, 2c, 2d, 2e.
- `make test` passes after each step.
- After 2d: `target/encoder_smoke.h264` exists after the
  test run, plays in `ffplay` (manual verification step,
  documented in the test).
- After 2e: the webrtc-rs H.264 packetiser unit test passes,
  OR a follow-up commit lands the VP8 alternative.
- `pre-commit run --all-files` passes.
- The encoder module is self-contained; no back-deps on
  ryll, no use of `egui`, no use of `eframe`. Verify with
  `grep -E "egui|eframe|ryll" shakenfist-spice-renderer/src/encoder/`
  returning nothing.
- Each of 2a–2e is a single commit on `thought-bubble` with
  a message that follows project conventions.

## Risks

- **openh264 API drift between versions.** `capture.rs`
  uses openh264 0.6. If a sub-agent reads outdated docs and
  imports a newer-version API, the build will fail. Anchor
  on what `capture.rs` already uses.
- **Annex-B vs MP4 framing confusion.** Easy to mix up.
  The brief is explicit but the test in 2d is the safety
  net (`ffplay` will reject malformed framing immediately).
- **`spawn_blocking` + tokio mpsc.** `blocking_recv` works
  inside `spawn_blocking` because the runtime allows
  blocking calls there. But `blocking_send` requires the
  channel not to be in a single-threaded local set. Keep
  channels at module / static scope (no `LocalSet`).
- **Frame-rate drift under load.** If encode takes >33 ms
  occasionally, the next tick is skipped rather than
  catching up. Acceptable for 30 fps. If we ever bump to
  60 fps and load matters, we'll need a smarter scheduler.
- **webrtc-rs H.264 packetiser surprises.** The pre-flight
  in 2e exists specifically to catch this. If the
  packetiser API does not accept the NAL framing we
  produce, the contingency is VP8. The risk is that VP8
  via `vpx-encode` has its own surprises, but those would
  be discovered before Phase 3 starts.
- **`webrtc-rtp` crate version churn.** The webrtc-rs
  ecosystem has been moving. Pin a version and verify it
  builds inside the devcontainer.

## Documentation updates

After 2e, update:

- `ARCHITECTURE.md` — note the new encoder module, where it
  sits in the data flow (DisplaySurface → FrameSource →
  H264Encoder → EncoderTask → mpsc<EncodedFrame> → Phase 3
  WebRTC), and the pacing model.
- `AGENTS.md` — note `make test` now exercises the encoder
  smoke test; mention `ffplay target/encoder_smoke.h264` as
  a manual verification step for the encoder.
- `docs/multi-mode-parity.md` — no row change yet; the
  encoder isn't user-visible until Phase 4 wires it up.
- `docs/plans/PLAN-web-frontend.md` — flip the Phase 2 row
  in the Execution table from "Not written" to "In progress"
  on first phase commit, "Complete" on 2e.
- `docs/plans/index.md` — Web frontend status row stays at
  "In progress"; phases-list line gets Phase 2 marked
  complete when 2e lands.

These doc updates can be batched into the 2e commit, or
done as a follow-up commit on top of 2e.

## Estimated total scope

Roughly 800–1200 lines of new code across five commits, the
bulk of it in 2b (H.264 encoder, ~300 lines) and 2c (async
task driver, ~200 lines). 2a is scaffolding (~50 lines),
2d is a test harness (~150 lines), 2e is a test (~100
lines) plus possibly a VP8 commit if the contingency
triggers (another ~300 lines).

## Back brief

Before executing 2a, the implementing agent should
back-brief: which dependency versions will be added, the
concrete `FrameSource` and `EncodedFrame` API shapes, and
how the agent intends to handle the tokio / `spawn_blocking`
boundary. Do not start editing without the back-brief.

Subsequent steps (2b–2e) follow the same pattern: back-brief
first, edit second.
