# Phase 5: Real frames, inputs, cursor, audio

## Prompt

Before responding to questions or making changes, explore the
codebase. Read the master plan at
`docs/plans/PLAN-web-frontend.md` (especially Resolutions §4
30 fps cap, §5 audio, §6 inputs, §7 cursor overlay) and the
Phases 1–4 plans. Key files:

- `shakenfist-spice-renderer/src/session.rs` — `run_connection`
  entry point that ryll's GUI and headless modes both spawn;
  Phase 4 deferred wiring this into `--web` mode.
- `shakenfist-spice-renderer/src/channels/mod.rs` —
  `ChannelEvent` enum (SurfaceCreated, ImageReady,
  ImageReadyChroma, ImageReadyAlpha, fill/copy/invert events,
  CursorPosition, CursorShape, etc.).
- `shakenfist-spice-renderer/src/channels/inputs.rs` — the
  `InputEvent` enum the channel consumes:
  `KeyDown(u32)`, `KeyUp(u32)`, `MouseMove{x,y}`,
  `MouseDown/Up{button,x,y}`, `PasteText{...}`. Phase 1 step 1a
  added `LogicalKey` and `scancode_for_logical_key()`.
- `shakenfist-spice-renderer/src/channels/playback.rs` — Opus
  packets are decoded to PCM (`opus-decoder`) before going
  into the cpal ring buffer. Phase 5 needs a tap point that
  forks raw Opus packets to web mode without affecting the
  GUI/headless cpal path.
- `shakenfist-spice-renderer/src/channels/cursor.rs` — emits
  `CursorPosition` and `CursorShape(CursorImage)` events.
  `CursorImage` carries width, height, hot_spot_x, hot_spot_y,
  and RGBA pixels.
- `ryll/src/app.rs` — its `process_events` loop is the
  reference dispatch from `ChannelEvent` to the GUI's surface
  map. Phase 5 either DRYs this up into a helper or
  duplicates the relevant subset for `--web` mode.
- `ryll/src/web/server.rs` and `ryll/src/web/signalling.rs`
  — Phase 4's HTTP server + bridge orchestration. The
  encoder is constructed in `EncoderInfra::restart` with a
  hardcoded 1280×720 `SyntheticFrameSource`. Phase 5 swaps
  these for the SPICE primary surface dimensions and a real
  frame source.
- `shakenfist-spice-renderer/src/encoder/frame_source.rs` —
  `FrameSource` trait + `FrameRef`. Phase 5 adds a third
  impl alongside `SyntheticFrameSource`: a surface-backed
  `RealFrameSource`.
- `ryll/src/main.rs::run_headless` — the canonical example
  of how to construct the trait-object scaffolding
  (`Arc<dyn TrafficSink>`, `Arc<dyn CaptureSink>`,
  `Arc<dyn NotificationSink>`, `LogConfig`, etc.) that
  `run_connection` consumes.
- `shakenfist-spice-webrtc/src/bridge.rs` — the bridge's
  `spawn_video_pump`, `spawn_synthetic_audio_pump`,
  `send_control`, `control_rx`. Phase 5 adds a real audio
  pump (replaces synthetic) and starts using `control_rx` for
  inbound input/resize/cursor messages.

External: RFC 6184 H.264 RTP (already in play), RFC 7587
Opus RTP (already in play), the SPICE vdagent
`VDAgentMonitorsConfig` flow (see `kerbside/docs/`).

Flag any uncertainty rather than guessing.

## Goal

End-to-end functioning SPICE → browser. After Phase 5:

- `ryll --web session.vv` connects to a real SPICE server.
  The renderer's session orchestrator runs the same way it
  does for `--headless` and the GUI.
- The browser shows the operator's actual desktop (not the
  synthetic test pattern). The encoder reads from a
  surface-backed `RealFrameSource` over the renderer's
  primary surface.
- Keyboard and mouse input from the browser reach the SPICE
  server. The browser builds its own
  `KeyboardEvent.code` → AT-scancode table and sends raw
  scancodes over the control datachannel; the Rust side
  deserialises and feeds them into the existing `inputs`
  channel via the renderer's `InputEvent` enum.
- The SPICE server's cursor (shape + position) is rendered
  in the browser as a CSS `<img>` overlay over the `<video>`
  element, driven by datachannel messages. The video frame
  itself does NOT carry the cursor.
- Audio works. When the SPICE server negotiated Opus
  (the common case), raw Opus packets are forwarded to the
  WebRTC audio track without re-encoding (master plan
  Resolution §5 path (a)). When the server negotiated raw
  PCM, PCM is encoded via `opus = 0.3` to drive the same
  audio track.
- Browser viewport size at connect time drives the SPICE
  guest resolution via `VDAgentMonitorsConfig`. Dynamic
  resize during a session is deferred to future work
  (encoder dimension changes mid-stream are non-trivial).

Out of scope for Phase 5:

- Dynamic resolution changes mid-session.
- Clipboard sync (master plan Future work).
- USB redirection (master plan out of MVP).
- Folder sharing (master plan out of MVP).
- Reconnect-on-PeerConnection-drop without dropping the
  SPICE session (Phase 6).
- Multi-monitor (Phase post-MVP).

## Scope

In:

- New trait impls in `ryll/` for the renderer's `Arc<dyn ...>`
  parameters that web mode needs but doesn't have in Phase 4:
  primarily a minimal `NotificationSink` (drops, or logs to
  stderr — web has no notification UI yet) and a no-op
  `TrafficSink` / `CaptureSink` where appropriate. Or share
  ryll's existing impls with no behaviour change.
- `ryll/src/web/session.rs` (new) — owns the spawned
  `run_connection` task, the surface mirror, and the input
  channel sender. Exposes a `SessionState` struct that the
  HTTP handlers and the encoder pipeline both consume.
- A `SurfaceMirror` (in either `ryll/src/web/` or, if it
  grows, `shakenfist-spice-renderer/src/`) that consumes
  `ChannelEvent` and maintains
  `HashMap<(u8, u32), DisplaySurface>`. Reuses the renderer's
  existing `DisplaySurface` draw-op API; the dispatch loop
  is small (~50 LoC) and doesn't justify a renderer-side
  helper extraction in Phase 5.
- `RealFrameSource` (in `shakenfist-spice-renderer/src/encoder/`):
  reads from the surface mirror under a short Mutex-lock,
  copies the primary surface into an internal RGBA buffer,
  returns `FrameRef`. Returns `None` when the surface is
  not dirty since the last call, so the encoder genuinely
  encode-on-dirty (master plan Resolution §4).
- Browser-side input handling: `KeyboardEvent.code` →
  AT-scancode JS table, `MouseEvent` → normalised
  coordinates, send as JSON over the control datachannel.
- Rust-side input deserialisation in
  `ryll/src/web/signalling.rs` (or a new `inputs.rs`):
  drains the bridge's `control_rx`, parses JSON, builds
  `InputEvent` values, sends via the renderer's `input_tx`.
- Browser viewport size at connect → `VDAgentMonitorsConfig`
  via the existing `resize_tx` flow that `run_connection`
  exposes. Sent once per session at offer time.
- Cursor overlay: a new `ryll/src/web/cursor.rs` (or fold
  into signalling.rs) that observes `ChannelEvent::CursorShape`
  and `ChannelEvent::CursorPosition`, encodes the cursor
  bitmap as PNG via the `image = "0.25"` crate already in
  the renderer, and sends shape + position JSON messages
  on the control DC. Browser-side: maintain a
  `<img id="cursor">` overlay, hide native cursor over
  `<video>`.
- Audio: a tap point in `playback.rs` that exposes raw
  Opus packets via a new `Arc<dyn OpusPacketSink>` trait
  (or a new `ChannelEvent::OpusPacket(...)` variant — pick
  one in 5e per the discussion below). The web mode wires
  this to `bridge.spawn_audio_pump(rx)`. The GUI/headless
  cpal path keeps using its existing decode-to-PCM behaviour
  unchanged.
- The PCM → Opus fallback for SPICE servers that negotiate
  raw PCM (master plan Resolution §5 path (b) fallback) via
  the `opus = 0.3` crate already in the webrtc crate's deps.
- Replace the bridge's `spawn_synthetic_audio_pump` call in
  `EncoderInfra::restart` with `spawn_audio_pump(rx)` driven
  by the new tap.
- Documentation: parity matrix update, plan status flips,
  Bugs-fixed entries for any bugs surfaced during execution.

Out:

- Dynamic resize, clipboard, USB, folder sharing,
  multi-monitor, multi-viewer (all out of MVP per master
  plan).
- The SurfaceMirror's `process_events` dispatch is
  duplicated from `ryll/src/app.rs::process_events` rather
  than DRY'd up. Future-work: extract a renderer-side
  `SurfaceMap` helper that both ryll's GUI and web modes
  consume, eliminating ~50 LoC of duplication. Tracked as
  a deferred item in this plan's Future work.

## Approach

### Crate / module placement

- **`SurfaceMirror`**: lives in `ryll/src/web/` (not the
  renderer crate). Justification: it's a thin event-dispatch
  glue, the renderer already exposes the draw-op primitives
  it needs, and putting it in the renderer would invite a
  premature abstraction across the GUI and web consumers.
  If it grows past ~100 LoC, lift to the renderer.
- **`RealFrameSource`**: lives in `shakenfist-spice-renderer/src/encoder/`
  alongside `SyntheticFrameSource`. It's a substrate-shaped
  thing — any SPICE→browser implementor would want it. The
  type signature is generic over a `Arc<Mutex<SurfaceMap>>`-
  shaped trait so the renderer doesn't depend on ryll's
  `SurfaceMirror` directly.
- **Audio Opus tap**: lives in `shakenfist-spice-renderer/src/channels/playback.rs`
  + a new `Arc<dyn OpusPacketSink>` trait in the renderer's
  `lib.rs` (similar shape to `TrafficSink` etc.). The web
  mode passes a real sink; GUI/headless pass `None`.
- **Cursor relay**: lives in `ryll/src/web/cursor.rs`. The
  cursor channel is already in the renderer; the relay-to-DC
  glue is web-specific.

### Surface mirror design

```rust
pub struct SurfaceMirror {
    /// (display_channel_id, surface_id) → DisplaySurface
    pub surfaces: HashMap<(u8, u32), DisplaySurface>,
    /// Sequence of dirty notifications since last
    /// drain — used by RealFrameSource to detect change.
    /// Simplest: rely on DisplaySurface::consume_dirty().
}

impl SurfaceMirror {
    pub fn new() -> Self { ... }
    pub fn apply_event(&mut self, event: &ChannelEvent) {
        match event {
            ChannelEvent::SurfaceCreated { display_channel_id, surface_id, width, height } => {
                self.surfaces.insert((*display_channel_id, *surface_id),
                    DisplaySurface::new(*surface_id, *width, *height));
            }
            ChannelEvent::SurfaceDestroyed { display_channel_id, surface_id } => {
                self.surfaces.remove(&(*display_channel_id, *surface_id));
            }
            ChannelEvent::ImageReady { display_channel_id, surface_id, left, top, width, height, pixels, .. } => {
                if let Some(s) = self.surfaces.get_mut(&(*display_channel_id, *surface_id)) {
                    s.blit(*left, *top, *width, *height, pixels);
                }
            }
            // ... ImageReadyChroma, ImageReadyAlpha, FillRect, CopyBits,
            //     Invert, FillSolid — match ryll/src/app.rs::process_events
            //     1:1 for the display-bearing variants ...
            _ => {}  // ignore non-display events at the mirror layer
        }
    }
    pub fn primary_surface(&self) -> Option<&DisplaySurface> {
        // Phase 4 picked (channel_id=0, surface_id=0) as the primary;
        // keep that. If absent, return any (one) surface.
        self.surfaces.get(&(0, 0))
            .or_else(|| self.surfaces.values().next())
    }
    pub fn primary_surface_mut(&mut self) -> Option<&mut DisplaySurface> { ... }
}
```

The dispatch list mirrors `ryll/src/app.rs::process_events`'s
display-bearing variants. Cursor and audio events go elsewhere
(separate observers — see 5d, 5e). The mirror does NOT own
GUI textures; it only stores raw pixels via `DisplaySurface`.

### `RealFrameSource`

```rust
pub struct RealFrameSource {
    mirror: Arc<tokio::sync::Mutex<SurfaceMirror>>,
    rgba_buf: Vec<u8>,
    last_dimensions: Option<(u32, u32)>,
    frame_idx: u64,
    epoch: std::time::Instant,
}

impl FrameSource for RealFrameSource {
    fn next_frame(&mut self) -> Option<FrameRef<'_>> {
        // Lock briefly, copy pixels, release.
        let mut guard = self.mirror.try_lock().ok()?;  // skip if busy
        let surface = guard.primary_surface_mut()?;
        if !surface.consume_dirty() {
            return None;  // no new frame
        }
        let (w, h) = surface.size();
        let pixels = surface.pixels();
        // Reuse rgba_buf where dimensions haven't changed.
        if self.last_dimensions != Some((w, h)) {
            self.rgba_buf.resize((w * h) as usize * 4, 0);
            self.last_dimensions = Some((w, h));
        }
        self.rgba_buf.copy_from_slice(pixels);
        drop(guard);

        let timestamp_us = self.epoch.elapsed().as_micros() as u64;
        self.frame_idx += 1;
        Some(FrameRef {
            width: w,
            height: h,
            rgba: &self.rgba_buf,
            timestamp_us,
        })
    }
}
```

`tokio::sync::Mutex` because the surface mirror is updated
from a tokio task (the ChannelEvent consumer); `try_lock`
because we'd rather skip a frame than block the encoder
thread on lock contention with the SPICE channel handler.
For Phase 5 MVP this is the right shape; a contended lock
showing up in profiling would be a Phase-6 perf item.

**Important**: `next_frame` is called from the encoder's
`spawn_blocking` thread. `try_lock` on a `tokio::sync::Mutex`
from a non-async context returns `Err` only if the lock is
held; `Mutex` is tokio's, so it doesn't deadlock with the
async runtime. Verify against tokio 1.x docs before
committing.

If `tokio::sync::Mutex::try_lock` from a blocking thread
turns out awkward, use `std::sync::Mutex` with
`try_lock`. The mirror's mutator is in an async task; an
async task holding `std::sync::Mutex::lock()` briefly is
fine if the critical section is short (microseconds).

### Browser → SPICE inputs

JSON wire format on the control DC:

```json
{ "type": "key", "scancode": 30, "down": true }
{ "type": "key", "scancode": 30, "down": false }
{ "type": "pointer-move", "x_norm": 0.523, "y_norm": 0.412 }
{ "type": "pointer-button", "button": "left", "down": true, "x_norm": 0.523, "y_norm": 0.412 }
```

`x_norm` / `y_norm` are `[0.0, 1.0]` over the video element.
The Rust side multiplies by the primary surface dimensions
to get absolute SPICE coordinates.

Browser-side `KeyboardEvent.code` → AT scancode table: a JS
port of the `scancode_for_logical_key()` table from
`shakenfist-spice-renderer/src/channels/inputs.rs`. Roughly
~100 entries (letters, digits, function keys, arrows,
modifiers, navigation, whitespace, common punctuation). The
JS table is hand-maintained; if a future addition lands
server-side it must also land client-side. Future-work item:
auto-generate the JS table from the Rust source via a
build script.

The control DC's `seed` channel is what the JS opens before
offer (Phase 3 finding). The bridge answers with its own
`control` DC (Phase 3 step 3e); messages flow on whichever
the browser has open. Verify: when the bridge's
`control_rx()` is drained, does it receive messages sent on
the browser-created `seed` channel, OR only on the
bridge-created `control` channel? The Phase 3e fan-in
should handle both. Confirm in 5c.

### Browser viewport → SPICE resolution

The browser sends one initial message after the PC reaches
`Connected`:

```json
{ "type": "viewport", "width": 1920, "height": 1080 }
```

The Rust side receives this, calls the existing
`resize_tx.send((1920, 1080))`. `run_connection` already
plumbs `resize_rx` into `MainChannel::run`, which sends
`VDAgentMonitorsConfig` to the SPICE server. Guest's
vdagent resizes. New `SurfaceCreated` events arrive at
the new resolution. The encoder restart on the next viewer
attach picks up the new dimensions automatically.

For Phase 5 MVP, browser sends viewport ONCE per session.
Dynamic mid-session resize is deferred (encoder restart
mid-stream is non-trivial; we'd need a pause-encoder /
swap-dimensions / resume flow plus a forced keyframe).

### Cursor overlay

Rust side observes `ChannelEvent::CursorShape(CursorImage)`
and `ChannelEvent::CursorPosition { x, y }` from the
ChannelEvent stream. Sends DC messages:

```json
{ "type": "cursor-shape", "png_b64": "...", "hot_x": 4, "hot_y": 4 }
{ "type": "cursor-pos", "x_norm": 0.523, "y_norm": 0.412 }
```

The PNG is base64-encoded for JSON-friendliness. The
`image = "0.25"` crate (already a renderer dep) does the
RGBA → PNG encoding.

Browser side: maintain a single `<img id="cursor">`. On
`cursor-shape`: set `img.src = "data:image/png;base64," + png_b64`,
adjust `style.transform = "translate(-{hot_x}px, -{hot_y}px)"`.
On `cursor-pos`: set `style.left` / `style.top` based on
the video element's bounding rect + `x_norm` / `y_norm`.

CSS: `cursor: none` over the `<video>` element so the
host browser cursor doesn't fight the overlay.

For Phase 5 MVP, the cursor overlay does NOT dynamically
re-fetch shapes that the SPICE server caches and references
by ID. The renderer's `cursor.rs` already caches; we just
forward the resolved shape every time the server sends
SET/INIT. Cache miss handling on the browser side is
future-work.

### Audio passthrough

Two cases per master plan Resolution §5:

- **Server negotiated Opus** (common case): we want to
  bypass the existing PCM-decode path entirely for web
  mode. Tap point in `playback.rs` BEFORE the
  `opus-decoder` call. The tap is a `Arc<dyn OpusPacketSink>`
  parameter — when `Some`, forward Opus packets directly
  with their SPICE-side timestamp; the rest of the channel
  still runs (cpal output is not used in web mode but is
  also not torn down — keeping the channel logic uniform).
  Actually in `--web` mode there's no cpal device available
  and the existing code path may panic on init. Need to
  verify and possibly skip cpal init in web mode.
- **Server negotiated PCM**: the existing code already
  decodes nothing (PCM is raw). Web mode encodes the PCM
  via `opus = 0.3` at 48 kHz mono, mirroring the synthetic
  audio pump's encoder. Same `OpusPacketSink` trait, just
  fed by an inline encoder.

Trait signature:

```rust
pub trait OpusPacketSink: Send + Sync {
    /// Forward one Opus packet to the consumer. `samples_in_packet`
    /// is typically 960 (20 ms at 48 kHz) — used by the consumer
    /// to derive RTP timestamps.
    fn on_opus_packet(&self, packet: &[u8], samples_in_packet: u32);
}
```

The web-mode impl pushes the packet onto an
`mpsc::Sender<OpusPacket>` that the bridge's audio pump
consumes.

For the PCM → Opus fallback, the web mode also needs to know
when to insert the encoder. Cleanest: make the
playback channel emit either Opus packets (when negotiated)
or PCM samples (when negotiated) — the trait grows a
`fn on_pcm_samples(...)` method, and the web-side impl
holds an Opus encoder it uses on the PCM path.

Trait signature v2:

```rust
pub trait OpusPacketSink: Send + Sync {
    fn on_opus_packet(&self, packet: &[u8], samples_in_packet: u32);
    fn on_pcm_samples(&self, samples: &[i16], sample_rate_hz: u32, channels: u8);
}
```

The web-mode impl on the PCM path: encode via `opus =
"0.3"` to 20 ms Opus packets at 48 kHz mono (downmix /
resample from whatever SPICE negotiated). For Phase 5 MVP,
the PCM path is the rare case (most modern SPICE servers
negotiate Opus); if downmix/resample is gnarly, ship the
trait and the GUI/headless behaviour intact, with the
web-side PCM impl emitting silence + a `tracing::warn!`
("PCM audio path not fully wired in --web for non-Opus
sessions; audio will be silent until Phase 5+ fallback
work"). Acceptable for MVP because xspice and QEMU both
negotiate Opus by default.

Decision in 5e: ship the Opus path proper; the PCM
fallback is "best-effort warning" until a follow-up.

### Hardcoded constants → SPICE-derived

In `ryll/src/web/signalling.rs::EncoderInfra::restart`,
Phase 4 hardcoded `WIDTH = 1280`, `HEIGHT = 720`, `FPS = 30`.
Phase 5 changes:

- `WIDTH` and `HEIGHT` come from the surface mirror's
  primary surface size at `restart` time. If no primary
  surface exists yet (browser connected before SPICE
  finished session-init), `restart` returns
  `Err("primary surface not yet available")`. The HTTP
  handler turns that into 503 Service Unavailable; browser
  retries.
- `FPS` stays 30. Master plan Resolution §4.

## Prerequisites

- Phase 4 complete on `thought-bubble`. (It is.)
- `rustls::crypto::ring::default_provider().install_default()`
  is called in `WebrtcBridge::new` (already there from the
  audit polish).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a   | high   | opus  | worktree | Wire `run_connection` into `run_web`. Build the trait scaffolding the renderer expects (`ByteCounter`, `TrafficBuffers` as `Arc<dyn TrafficSink>` (or a no-op alternative), `Arc<dyn CaptureSink>`, `Arc<dyn NotificationSink>` (a minimal impl that drops or stderr-logs), `Arc<dyn ClipboardBackend>` (`None` is acceptable per Phase 4 path), `LogConfig`, `Arc<AtomicBool>` cancel flag bridged from `SHUTDOWN_REQUESTED` via the same pattern as `run_headless`). Spawn `run_connection` on a tokio task. Drain its `event_rx` in a separate task that does nothing yet (just consumes events; 5b adds the surface mirror). Acceptance: `ryll --web session.vv` connects to a real SPICE server (visible in logs: "main: channel started", "main: agent connected"); browser still shows the synthetic test pattern. Worktree because this touches the most-coupled wiring code in the project. Single commit. |
| 5b   | high   | opus  | worktree | Add `SurfaceMirror` (in `ryll/src/web/surface_mirror.rs`) consuming `ChannelEvent`s. Add `RealFrameSource` (in `shakenfist-spice-renderer/src/encoder/frame_source.rs` alongside `SyntheticFrameSource`) parameterised over a `Arc<Mutex<SurfaceMirror>>`-shaped backing — actually the trait `FrameSource` doesn't need to know about the mirror; just give `RealFrameSource` an `Arc<tokio::sync::Mutex<SurfaceMirror>>` directly. Refactor `EncoderInfra::restart` to read the primary surface dimensions from the mirror at restart time; return `Err("primary surface not yet available")` if absent so /offer returns 503. Replace the `SyntheticFrameSource::new(WIDTH, HEIGHT)` call with `RealFrameSource::new(state.surface_mirror.clone())`. Acceptance: `ryll --web session.vv` connects to SPICE, browser shows the actual desktop (in real time). Worktree. Single commit. |
| 5c   | high   | opus  | worktree | Browser inputs over the control DC. (1) JS: build the `KeyboardEvent.code` → AT scancode table (port from `shakenfist_spice_renderer::channels::inputs::scancode_for_logical_key`). Capture `keydown`/`keyup` on the document; build the JSON envelope `{"type":"key","scancode":...,"down":...}`; send via the `seed` data channel. (2) JS: capture mousemove/mousedown/mouseup on the `<video>` element; convert client coordinates to `[0.0, 1.0]` normalised over the video's bounding rect; send `{"type":"pointer-move","x_norm":...,"y_norm":...}` and `{"type":"pointer-button",...}`. (3) JS: on PC reaching connected, send the initial viewport message `{"type":"viewport","width":...,"height":...}`. (4) Rust: in `ryll/src/web/inputs.rs` (new), spawn a task that drains `bridge.control_rx()`, parses JSON, builds `InputEvent` values via `LogicalKey` (or sends raw scancodes through the same channel — read inputs.rs for the existing `InputEvent::KeyDown(u32)` shape), and sends via the renderer's `input_tx`. (5) Rust: route the viewport message through `resize_tx` so `MainChannel` sends `VDAgentMonitorsConfig`. Acceptance: open the browser, type, click, watch the SPICE server respond; browser viewport matches guest resolution after the first connect. Worktree. Single commit. |
| 5d   | medium | opus  | worktree | Cursor overlay. (1) Rust: in `ryll/src/web/cursor.rs` (new), spawn a task that observes the channel-event stream for `CursorShape` and `CursorPosition`. Encode shape RGBA → PNG via `image = "0.25"` (already a renderer dep), base64-encode, send `{"type":"cursor-shape","png_b64":...,"hot_x":...,"hot_y":...}`. For position, send `{"type":"cursor-pos","x_norm":...,"y_norm":...}` (normalise by primary surface dimensions). (2) Browser: maintain a single `<img id="cursor">` overlay. On `cursor-shape`, set `img.src` to the data URI and apply `style.transform = "translate(-{hot_x}px, -{hot_y}px)"`. On `cursor-pos`, position via `style.left` / `style.top` based on the video's `getBoundingClientRect()`. Add CSS: `#cursor { position: absolute; pointer-events: none; }` and `#video { cursor: none; }` so the host cursor doesn't fight the overlay. Acceptance: move the mouse around in the browser window over the video, observe the SPICE-side cursor shape and position track in the overlay. Single commit. |
| 5e   | high   | opus  | worktree | Audio passthrough. (1) Renderer: add the `OpusPacketSink` trait to `shakenfist-spice-renderer/src/lib.rs`. (2) Renderer: in `playback.rs`, accept `Option<Arc<dyn OpusPacketSink>>` as a constructor parameter; in the receive loop, when a packet arrives BEFORE Opus decode, if the sink is `Some` call `sink.on_opus_packet(packet, samples_per_packet)`. The cpal-output side keeps decoding to PCM as before. (3) Webrtc bridge: replace `spawn_synthetic_audio_pump` with a generic `spawn_audio_pump(rx: mpsc::Receiver<OpusPacket>) -> JoinHandle<Result<()>>` that consumes Opus packets from the channel and writes RTP. The sender side is implemented in ryll: a small `WebOpusSink` that pushes onto an `mpsc::Sender<OpusPacket>` cloned per active bridge. (4) ryll: wire the sink. The PCM-fallback case (server negotiated raw PCM): for Phase 5 MVP, ship a `tracing::warn!("Web mode currently requires Opus-negotiated SPICE audio; PCM session detected, audio will be silent")` and continue. Acceptance: open the browser to a SPICE server with audio (xspice/QEMU defaults to Opus), hear the actual desktop audio. Worktree. Single commit. |
| 5f   | medium | sonnet | none | Documentation. Update `docs/multi-mode-parity.md` to flip the web rows from "missing (Phase 5)" to "available". Add Bugs-fixed entries for any bugs surfaced during 5a–5e. Update `docs/plans/PLAN-web-frontend.md` execution table — Phase 5 row → Complete. Update `docs/plans/index.md` web frontend status. Update `ARCHITECTURE.md` and `AGENTS.md` if 5a–5e introduced new substrate (e.g., the `OpusPacketSink` trait — yes, document it; the `SurfaceMirror` — note where it lives). Add a small operator quick-start to `docs/web-frontend.md` (NEW): "ryll --web session.vv → open the URL → done", including a mention that audio requires Opus negotiation and that Ctrl-C now cleanly stops the binary (fix from the post-Phase-4 audit). Single commit. |

After 5f, Phase 5 is done. The web frontend is operationally
usable end-to-end against a real SPICE server.

## Step details

### Step 5a expanded brief

The `run_connection` signature (in
`shakenfist-spice-renderer/src/session.rs`) takes:

- `ConnectionConfig` (from `shakenfist_spice_protocol`)
- `event_tx: mpsc::Sender<ChannelEvent>`
- `repaint_notify: Arc<Notify>` — for the GUI; web mode can
  pass an unused `Arc::new(Notify::new())` and never read.
- `input_rx: mpsc::Receiver<InputEvent>`
- `usb_rx`, `webdav_rx` — empty receivers for web mode (no
  USB or WebDAV in MVP)
- `virtual_disks: Vec<VirtualDiskConfig>` — empty
- `share_dir: Option<ShareDirConfig>` — None
- `capture: Option<Arc<dyn CaptureSink>>` — None unless the
  user passed `--capture`
- `byte_counter: Arc<ByteCounter>` — fresh
- `traffic: Arc<dyn TrafficSink>` — fresh `TrafficBuffers`,
  same shape as `run_headless`
- `snapshots: ChannelSnapshots` — fresh
- `monitors: u8` — `args.monitors` (typically 1)
- `resize_rx: mpsc::Receiver<(u32, u32)>` — created here;
  the sender is held in `WebState` for the inputs/viewport
  flow in 5c
- `volume_control: Arc<VolumeControl>` — fresh
- `enable_paste: bool` — false
- `log_config: LogConfig` — derived from CLI args
- `cancel: Arc<AtomicBool>` — bridged from
  `SHUTDOWN_REQUESTED` via the same pattern as
  `run_headless` (a separate task that polls on 100ms and
  flips the cancel flag)
- `clipboard: Option<Arc<dyn ClipboardBackend>>` — `None`
  for web mode (no clipboard sync in MVP).

That's a lot of plumbing. Look at `run_headless` for the
canonical pattern; web mode's setup is essentially a copy
with the .vv-driven config replacing the no-op stub from
Phase 4.

The `event_rx` consumer in 5a is a stub:

```rust
tokio::spawn(async move {
    while let Some(event) = event_rx.recv().await {
        // 5b adds: surface_mirror.lock().await.apply_event(&event);
        // 5d adds: cursor relay
        // 5e adds: audio packet observation (or via OpusPacketSink trait)
        let _ = event;
    }
});
```

### Step 5b expanded brief

The `SurfaceMirror::apply_event` dispatch should handle (at
minimum, for the desktop to look correct):

- `SurfaceCreated` → `surfaces.insert(key, DisplaySurface::new(...))`
- `SurfaceDestroyed` → `surfaces.remove(key)`
- `ImageReady` / `ImageReadyChroma` / `ImageReadyAlpha` →
  the corresponding `DisplaySurface::blit*` method
- `FillRect` / `FillSolid` → the corresponding fill method
- `CopyBits` → `DisplaySurface::copy_bits`
- `Invert` → `DisplaySurface::invert_rect`

Read `ryll/src/app.rs::process_events` for the canonical
match — copy structure, drop the egui `repaint_notify` hooks
and the GUI-only state mutations.

If `process_events` turns out to also call into bug-report
or notification machinery from inside the match arms, do
NOT replicate those calls — web mode doesn't need them.
The mirror is just for pixel state.

The `RealFrameSource::next_frame` interaction with
`tokio::sync::Mutex::try_lock`: this is the trickiest piece.
`tokio::sync::Mutex::try_lock` is a synchronous method (no
await), so it's safe to call from a non-async context like
the encoder's blocking thread. Verify against tokio 1.x
docs. If awkward, fall back to `std::sync::Mutex` — the
critical sections in both the apply_event task and the
next_frame call are short.

### Step 5c expanded brief

Browser-side `KeyboardEvent.code` → AT-scancode table: read
`shakenfist-spice-renderer/src/channels/inputs.rs` for the
existing `scancode_for_logical_key` table. The JS table is
~100 entries; transcribe carefully, and keep them in the
same order so future updates are easy to replay.

Suggested JS structure:

```javascript
const SCANCODES = {
    "KeyA": 0x1E, "KeyB": 0x30, ...,
    "Digit1": 0x02, ...,
    "F1": 0x3B, ..., "F12": 0x58,
    "ArrowUp": 0xE048, "ArrowDown": 0xE050, ...,
    "ShiftLeft": 0x2A, "ShiftRight": 0x36,
    "ControlLeft": 0x1D, "ControlRight": 0xE01D,
    ...
};

document.addEventListener("keydown", e => {
    const sc = SCANCODES[e.code];
    if (!sc) return;  // ignore unknown keys
    e.preventDefault();
    sendControl({ type: "key", scancode: sc, down: true });
});
document.addEventListener("keyup", e => {
    const sc = SCANCODES[e.code];
    if (!sc) return;
    e.preventDefault();
    sendControl({ type: "key", scancode: sc, down: false });
});
```

Note that some scancodes are 16-bit (E0-prefixed for
extended keys). The JS protocol can either:

- Send the 16-bit value directly: `{"scancode": 0xE048, "down": true}`. Rust side
  splits at deserialisation.
- Send a tagged form: `{"scancode": 0x48, "extended": true}`.
  Rust side reconstructs.

Pick the first — simpler wire format. The renderer's
`InputEvent::KeyDown(u32)` already carries a u32 so the full
value fits.

Pointer events: convert `MouseEvent.clientX` /
`MouseEvent.clientY` to normalised coordinates relative to
the video element's `getBoundingClientRect()`. The video
element scales letterboxed; account for the actual rendered
video area within the element to avoid coordinate skew.

### Step 5d expanded brief

The cursor relay task on the Rust side:

```rust
async fn cursor_relay(
    mut event_rx: broadcast::Receiver<ChannelEvent>,  // or mpsc
    bridge_slot: Arc<Mutex<Option<WebrtcBridge>>>,
    primary_dims: Arc<Mutex<Option<(u32, u32)>>>,
) {
    while let Ok(event) = event_rx.recv().await {
        match event {
            ChannelEvent::CursorShape(img) => {
                let png = encode_png_b64(&img);
                let msg = json!({
                    "type": "cursor-shape",
                    "png_b64": png,
                    "hot_x": img.hot_spot_x,
                    "hot_y": img.hot_spot_y,
                });
                if let Some(bridge) = bridge_slot.lock().await.as_ref() {
                    let _ = bridge.send_control(msg.to_string().as_bytes()).await;
                }
            }
            ChannelEvent::CursorPosition { x, y } => {
                let dims = primary_dims.lock().await.unwrap_or((1, 1));
                let msg = json!({
                    "type": "cursor-pos",
                    "x_norm": (x as f32) / (dims.0 as f32),
                    "y_norm": (y as f32) / (dims.1 as f32),
                });
                if let Some(bridge) = bridge_slot.lock().await.as_ref() {
                    let _ = bridge.send_control(msg.to_string().as_bytes()).await;
                }
            }
            _ => {}
        }
    }
}
```

Two concerns: (a) the event channel has multiple consumers
in 5b/5d/5e (surface mirror, cursor relay, audio sink). You
either need a broadcast channel or each consumer pulls from
the same mpsc and ignores events it doesn't care about — but
mpsc is single-consumer. **Pick `tokio::sync::broadcast`**
upstream of the consumers; the apply_event tasks subscribe.
That's a small refactor of 5a's stub consumer.

PNG encoding via `image = "0.25"`:

```rust
fn encode_png_b64(img: &CursorImage) -> String {
    let png = image::codecs::png::PngEncoder::new(...);
    // ... encode RGBA8 to PNG ...
    base64::encode(&png_bytes)
}
```

Add `base64 = "0.22"` as a renderer dep if not present.

### Step 5e expanded brief

Tap point in `playback.rs`. Read the existing audio path
carefully — the channel decodes Opus to PCM into an `rtrb`
ring buffer that cpal consumes. The tap belongs immediately
after the SPICE-side packet is received, BEFORE the Opus
decode.

In the `--web` mode there's no cpal output device. Verify
that the existing playback channel doesn't panic when cpal
init fails — there's an audit-deferred TODO ("Audio in
headless is a silent trap") suggesting this path is broken
even in headless. Phase 5 may need to make the cpal init
optional via a new `Option<Arc<dyn AudioOutput>>` parameter
or similar. If that's a bigger refactor than fits 5e,
flag and reduce scope (ship audio working; cpal init may
log a warning but the channel proceeds).

The PCM fallback: SPICE servers that negotiate raw PCM
instead of Opus. Phase 5 ships a `warn!` and silent audio
in this case. Document in the commit body. Phase 6 or a
follow-up plan handles the PCM → Opus encode path properly.

## Acceptance criteria

- `make lint` and `make test` pass after each of 5a–5f.
- After 5b: `ryll --web session.vv` against a real SPICE
  server shows the actual desktop in the browser.
- After 5c: keyboard and mouse work end-to-end.
- After 5d: SPICE cursor renders as an overlay tracking the
  guest cursor.
- After 5e: audio is audible (when SPICE negotiated Opus).
- `pre-commit run --all-files` passes.
- The renderer crate stays free of egui/eframe references
  (`grep -E "egui|eframe" shakenfist-spice-renderer/src/`
  returns only doc comments).
- Each of 5a–5f is a single commit on `thought-bubble`.

## Risks

- **`tokio::sync::Mutex::try_lock` from a `spawn_blocking`
  thread.** Verify in 5b. Fallback: `std::sync::Mutex`. If
  both turn out problematic (e.g. lock contention starves
  the encoder), use a lock-free SPSC ring buffer between the
  apply_event task and a pre-copied RGBA mirror.
- **Browser DC ordering: seed (browser-created) vs control
  (bridge-created).** Phase 3 step 3e wired both into the
  same `incoming_control` mpsc. Verify in 5c that messages
  sent on the seed channel actually reach `control_rx()` on
  the Rust side.
- **Coordinate normalisation for letterboxed video.** The
  `<video>` element scales the stream; the actually-rendered
  area may be smaller than the element's bounding rect (with
  letterbox bars). Compute the rendered area explicitly from
  `videoEl.videoWidth` / `videoEl.videoHeight` and the
  element's bounding rect rather than just using the rect.
- **Encoder-restart timing on viewport change.** Phase 4
  encoder-per-viewer means the encoder gets fresh dimensions
  on each `/offer`. The browser's initial viewport message
  (5c) needs to land on the resize_rx BEFORE the next
  /offer's encoder restart, otherwise the first offer
  encodes at xspice's default resolution and the browser
  scales. For MVP that's acceptable; the second viewer
  attach picks up the new resolution.
- **`--web` mode connecting to a SPICE server with no
  audio.** xspice typically has no audio device by default;
  `playback.rs` may emit no events at all, the audio pump
  buffers nothing, the bridge's audio track is silent. Test
  with QEMU-SPICE which does have audio.
- **Chained worktree base resets.** Several Phase 5 steps
  use worktree isolation. Each must `git fetch origin &&
  git reset --hard thought-bubble` at start.

## Documentation updates

After 5f:

- `ARCHITECTURE.md` — section on the SurfaceMirror, the
  RealFrameSource, the OpusPacketSink trait, and the
  end-to-end data flow from SPICE channels through the
  encoder to the browser.
- `AGENTS.md` — note the new traits, any new deps
  (`base64`?), and the new `ryll/src/web/{surface_mirror,
  inputs, cursor}.rs` modules.
- `README.md` — the web frontend is now usable end-to-end;
  add a "Quick start: ryll --web" section.
- `docs/web-frontend.md` (NEW per master plan Phase 8 — but
  Phase 5 ships a minimal version): operator quick-start.
  Phase 8 expands it with systemd, troubleshooting, security.
- `docs/multi-mode-parity.md` — flip the missing rows.
- `docs/plans/PLAN-web-frontend.md` — Phase 5 row Complete;
  any audit deferrals carried into Future work.
- `docs/plans/index.md` — Phase 5 marker.

## Estimated total scope

Roughly 2000–2500 lines across six commits. Heaviest in 5a
(orchestrator wiring, ~500 LoC including the trait
scaffolding boilerplate copied from `run_headless`), 5b
(SurfaceMirror dispatch + RealFrameSource + tests, ~400),
5c (JS scancode table + Rust deserialisation +
input_tx/resize_tx wiring, ~500). 5d ~250. 5e ~300. 5f
~200.

## Back brief

Before executing 5a, the implementing agent should
back-brief: which trait impls in `ryll/src/` get reused
vs newly written, where the tokio task graph for
event_rx draining lives (broadcast channel? single mpsc
with priority dispatch?), and how `cancel: Arc<AtomicBool>`
is bridged from `SHUTDOWN_REQUESTED` in web mode (separate
poll task vs reusing the existing main.rs:283 pattern).

Subsequent steps follow the same pattern: back-brief first,
edit second.
