# Web mode internals

How `ryll --web` turns a live SPICE session into something a browser can
consume: the H.264 encoder pipeline, the WebRTC bridge, the four real-data
relays that connect them to the SPICE channels, and the lifecycle and
packaging concerns around them.

This is the developer-facing companion to the
[`--web` operator guide](/components/ryll/web-frontend/), which covers running the thing.
See [Architecture](/components/ryll/../ARCHITECTURE/) for where these crates sit overall.

## Encoder module

`shakenfist-spice-renderer/src/encoder/` is the live H.264 encoder
pipeline.

### `H264Encoder`

A stateful wrapper around openh264. Takes an RGBA pixel buffer,
converts to YUV 4:2:0, encodes to Annex-B framed NAL units
(each NAL prefixed with the 4-byte start code `00 00 00 01`).
Every IDR frame is accompanied by SPS (NAL type 7) and PPS
(NAL type 8) NALs so keyframes are self-contained. Even-dimension
constraint: width and height are rounded down to even numbers
before encoding. The `force_keyframe: bool` parameter to
`encode()` calls openh264's `force_intra_frame()` for the
next encode.

### `EncoderTask`

Async driver that lives on tokio's blocking pool (openh264 is a
synchronous C library; `spawn_blocking` keeps it off the async
executor). The task loop:

- Ticks at a configurable FPS cap (default 30; period ≈ 33 333 µs).
- On each tick, calls `source.next_frame()`. If `Some`, encodes
  and sends `EncodedFrame` on the output channel. If `None`,
  skips the tick — no idle frames are produced.
- Handles `EncoderControl::RequestKeyframe` by setting a
  `keyframe_pending` flag consumed on the next encode.
- Handles `EncoderControl::Stop` by breaking the loop.

### `FrameSource` and `FrameRef`

`FrameSource` is a trait that decouples the encoder from how
pixels arrive. The implementing type handles dirty tracking and
synchronisation with concurrent display-channel writers. It
returns `Option<FrameRef<'_>>` where `FrameRef` carries width,
height, RGBA bytes, and a `timestamp_us` used to derive RTP
timestamps. `SyntheticFrameSource` is a test/CI source that
generates animated gradient frames.

## WebRTC bridge (`shakenfist-spice-webrtc`)

`shakenfist-spice-webrtc` is a separate crate (not part of the
renderer) because the webrtc-rs dependency tree (DTLS, SRTP,
ICE, SCTP, STUN) is heavy and not all SPICE-client consumers
need it. The renderer stays a pure SPICE substrate; the bridge
is one specific delivery mechanism.

### `WebrtcBridge`

Wraps an `RTCPeerConnection` and owns:

- A video `TrackLocalStaticRTP` (H.264, 90 kHz clock rate).
- An audio `TrackLocalStaticRTP` (Opus, 48 kHz clock rate).
- A "control" `RTCDataChannel` (ordered + reliable).
- An `mpsc::Sender<EncoderControl>` to request keyframes.

Construction via `WebrtcBridge::new(WebrtcBridgeConfig)`:
builds the PC via webrtc-rs's `APIBuilder` + `MediaEngine`
pattern, registers H.264 and Opus codecs, creates both tracks,
adds them to the PC, creates the control DC, and registers three
callbacks — connection state change, ICE gathering state change,
and control-DC message — all delegating to one `BridgeEvents`
struct. `BridgeEvents` is deliberately the shape of webrtc-rs
0.20's `PeerConnectionEventHandler` trait, so the 0.20 port adds
an `impl` and deletes the closures without the bodies moving.

The state-change handler shadows the latest
`RTCPeerConnectionState` (the inherent accessor does not survive
the 0.20 port) and raises the sticky `dead` signal on the first
terminal transition. The gathering handler raises the sticky
`gathered` signal on `Complete`. Both signals are
`StickySignal`s (`sticky.rs`): a `Notify` + sticky `AtomicBool`
pair giving level-triggered, raise-exactly-once semantics — see
the WebRTC conventions section of AGENTS.md for why a bare
`Notify` is not safe here.

`WebrtcBridgeConfig` carries the ICE server list (empty for
LAN-only use) and the `EncoderControl` sender.

### SDP flow

`accept_offer(sdp: String) -> Result<String>` is the single SDP
entry point for the HTTP `/offer` handler. It:

1. Sets the remote description (browser's offer).
2. Creates an answer.
3. Sets the local description.
4. Waits for ICE gathering to complete, by awaiting the sticky
   `gathered` signal raised by `BridgeEvents` (webrtc-rs 0.17's
   `gathering_complete_promise()` does not exist in 0.20).
5. Returns the fully-resolved answer SDP.

ICE gathering completion is awaited so the answer already
contains all host candidates — trickle ICE is not needed for
LAN-only use. Because the gathering signal is sticky and
never resets, a `WebrtcBridge` handles exactly one offer/answer
exchange; renegotiation requires a new bridge, and the web
frontend constructs one per `POST /offer`.

### Video pump

`spawn_video_pump(rx: mpsc::Receiver<EncodedFrame>)` drives the
video track:

- Consumes `EncodedFrame`s from the encoder output channel.
- Strips Annex-B start codes from each NAL.
- Payloads raw NALs via `H264Payloader` (from `rtp::codecs::h264`).
- Sets the `marker` bit on the last RTP packet of each access unit
  (per RFC 6184 §5.1 — decoder pacing depends on this).
- Derives RTP timestamps from `EncodedFrame::timestamp_us` at
  90 kHz: `rtp_ts = (timestamp_us × 90_000) / 1_000_000`.
- Sequence numbers increment via `wrapping_add`.

SPS/PPS NALs produce empty payload sets (the `H264Payloader`
caches them and bundles them as STAP-A with the next IDR slice);
the pump skips empty sets cleanly.

### Control datachannel

Ordered + reliable, labelled "control". It carries ping/pong for
smoke testing, input events (scancodes, pointer coordinates), and
cursor overlay updates. `send_control(&[u8])` and `control_rx()`
are the public API.

`spawn_synthetic_audio_pump()` remains available for testing
without a SPICE server: it emits a 440 Hz sine wave encoded as
Opus at 50 fps (20 ms per frame, 960 samples at 48 kHz), through
the same `TrackLocalStaticRTP` consumer interface the real audio
path uses.

### Keyframe-on-attach

The bridge sends `EncoderControl::RequestKeyframe` when the
`RTCPeerConnection` transitions to the `Connected` state, so the
first frame the browser sees is always a full IDR. A PLI
(Picture Loss Indication) RTCP handler is also registered for
the same purpose when a viewer requests a refresh.

### webrtc-rs convention: `on_track` must spawn a task

See the "WebRTC conventions" section in `AGENTS.md` for the normative rule regarding `on_track` and `read_rtp`.

## SPICE wire-up

Four connections carry real data between the SPICE session and the
bridge: display frames, audio, keyboard/mouse input, and cursor overlay.

### `SurfaceMirror`

`shakenfist-spice-renderer/src/surface_mirror.rs` — subscribes
to the renderer's broadcast `ChannelEvent` stream and maintains
a `HashMap<(u8, u32), DisplaySurface>` keyed by
`(channel_id, surface_id)`. The mirror is the authoritative
surface state for the web encoder path; it is separate from
the `RyllApp` surface map so the `--web` mode can run without
any egui dependency.

### `RealFrameSource`

`shakenfist-spice-renderer/src/encoder/frame_source.rs` — a
`FrameSource` implementation that reads from a `SurfaceMirror`
under `try_lock`. Returns `None` on lock contention (the encoder
skips the tick rather than blocking) and also returns `None` when
the primary surface is not dirty, achieving genuine
encode-on-dirty behaviour within the 30 fps cap.

### `OpusPacketSink` trait

`shakenfist-spice-renderer/src/audio_sink.rs` — a pre-decode
tap on the SPICE playback channel. When a type implementing this
trait is injected into the playback channel constructor, raw Opus
packets from the SPICE server are delivered to `push_opus_packet`
before being decoded to PCM for the cpal path. The `--web` mode
uses this to route Opus packets to the WebRTC audio track without
re-encoding. When the SPICE server negotiates raw PCM (not Opus),
the sink receives no packets; the web audio track is silent (a
warning is logged).

### Input relay

`ryll/src/web/inputs.rs` — drains the bridge's control
datachannel, parses the JSON input events that the browser shell
posts, and emits `InputEvent` variants (key down/up with AT
scancodes, mouse position/button) into the renderer's existing
inputs channel handler. Viewport-resize messages from the browser
are forwarded to `maybe_send_monitors_resize` so the SPICE guest
can track the browser viewport size at connect time.

### Cursor relay

`ryll/src/web/cursor.rs` — subscribes to the renderer's
broadcast `ChannelEvent` stream and watches for `CursorImage`
and `CursorPos` events (the same events the egui frontend
consumes). Cursor shapes are encoded as PNG (`base64 = "0.22"`
for the data-URL wrapper) and sent as JSON over the control
datachannel. The browser shell decodes the data-URL, updates
an `<img>` overlay element, and repositions it to follow cursor
motion events — keeping cursor latency on the datachannel path
rather than the video encoder path.

### Audio adapter

`ryll/src/web/audio.rs` — `WebOpusSink` implements
`OpusPacketSink`. When the `RTCPeerConnection` reaches
`Connected`, the bridge activates the audio pump; `WebOpusSink`
routes each incoming Opus packet to the bridge's audio track
via the WebRTC audio pump. PCM-only SPICE servers do not
trigger any `push_opus_packet` calls, so the audio track emits
silence (and a one-time warning is logged).

### End-to-end data flow

```
SPICE server
    │
    ▼
shakenfist-spice-renderer::run_connection
    │
    ├─► DisplayChannel ──► broadcast ChannelEvent ──► SurfaceMirror
    │                                              └─► CursorRelay (cursor.rs)
    │
    ├─► PlaybackChannel ──► OpusPacketSink (audio.rs) ──► WebRTC audio track
    │
    └─► InputsChannel ◄── web inputs relay (inputs.rs) ◄── control DC ◄── browser
                                                                              │
shakenfist-spice-webrtc::WebrtcBridge                                         │
    ├─► video track ◄── EncoderTask ◄── RealFrameSource ◄── SurfaceMirror    │
    ├─► audio track ◄── WebOpusSink ◄── PlaybackChannel (Opus path)          │
    └─► control DC ◄──────────────────────────────────── cursor/input relay ──┘
    │
    ▼
Browser (RTCPeerConnection)
    ├─ <video> H.264 display
    ├─ <audio> Opus audio
    └─ datachannel: cursor overlay + input events
```

The `on_track`-must-spawn-a-task webrtc-rs idiom (documented
above) and the rustls `CryptoProvider` init (required once at
process start, before any TLS handshake) both apply to the
`--web` mode and are handled in `ryll/src/main.rs` before
`run_web()` is called.

## Bridge lifecycle

### Bridge dead signal

`WebrtcBridge` carries a `dead: Arc<StickySignal>` field —
raised exactly once when the `RTCPeerConnection` reaches
`Failed`, `Disconnected`, or `Closed`. `StickySignal`
(`sticky.rs`) pairs a `Notify` with a sticky `AtomicBool`, so
late callers do not wait on an already-dead bridge and a raise
landing mid-subscribe is not lost.

Public API:

- `wait_for_dead(&self) -> impl Future` — resolves when the
  PC reaches a terminal state. Returns immediately if the
  signal is already raised (late-subscriber safety).
- `dead_signal(&self) -> Arc<StickySignal>` — exposes the
  signal for consumers that hold their own clone without
  keeping a reference to the bridge; `handle.wait().await`
  is equivalent to `wait_for_dead()`.

### Server-side reaper (`ryll/src/web/lifecycle.rs`)

`run_bridge_reaper(state: Arc<WebState>)` is a long-lived
task spawned from `run_web`. Its loop:

1. Clones the active bridge's `dead_signal()` without
   holding the slot lock for long.
2. If no bridge is active, sleeps 500 ms and retries.
3. Awaits the dead signal.
4. Takes the bridge out of `bridge_slot`, calls
   `bridge.close().await`.
5. Calls `EncoderInfra::stop()` — sends `EncoderControl::Stop`
   and awaits the encoder task handle (2-second ceiling).
6. Clears `opus_active_tx`.

The SPICE session (`run_connection`) is left completely
untouched. A subsequent `/offer` from the browser rebuilds
a fresh bridge and encoder from the same live SPICE state.

Race condition: a new `/offer` and the reaper both race to
take the bridge via `bridge_slot.lock()`. Both serialise on
the mutex; whichever arrives first takes the slot, the other
observes `None` and no-ops.

### `EncoderInfra::stop`

A helper alongside `restart()`: sends `EncoderControl::Stop`
to the running encoder task and joins the handle with a
2-second timeout. Used by both the reaper (bridge died) and
the shutdown path (process exiting). Any orphaned task exits
naturally on its next send error.

### Graceful shutdown sequence

`run_web`'s shutdown sequence is:

1. Ctrl-C → `SHUTDOWN_REQUESTED.store(true)`.
2. The bridge between `SHUTDOWN_REQUESTED` and the SPICE
   `cancel` flag flips the cancel.
3. `axum::serve(…).with_graceful_shutdown(…).await` drains.
4. Take the active bridge from the slot and close it
   (2-second ceiling) so DTLS/SRTP tears down cleanly.
5. Call `EncoderInfra::stop()` to release the encoder task.
6. `run_web` returns; the tokio runtime drops.

### Browser-side auto-reconnect

`app.js` keeps the `RTCPeerConnection` setup and SDP offer flow
inside a callable `connect()` function rather than a one-shot
IIFE. On ICE-failed or connection-state-failed events,
`scheduleReconnect()` is called with backoff delays of 1 s, 2 s,
4 s, 8 s, 16 s (max 5 attempts). A hidden "Click to reconnect"
button is revealed when all attempts are exhausted. Each attempt
constructs a brand-new `RTCPeerConnection`; the backoff counter
resets on a successful `Connected` transition.

## CI and packaging

- `shakenfist-spice-renderer` and `shakenfist-spice-webrtc`
  are in the `publish-crates` step in `release.yml`
  in dependency order (renderer after compression; webrtc
  after usbredir; ryll last).
- `libopus-dev` is present in the devcontainer (and installed
  on the aarch64 Linux CI runner) so the `opus` crate
  dynamic-links against a system libopus. The deb/rpm
  packaging metadata records `libopus0` as a runtime
  dependency. On macOS and Windows the `audiopus_sys`
  source-build fallback applies (pkg-config absent → compile
  from source → no runtime dep).
- `tools/web-smoke.sh` is a CI step on the Linux x86_64
  build job only (`make web-smoke` / `make web-smoke-tls`,
  run inside the devcontainer). It launches `ryll --web`
  with a stub `.vv`, asserts the process stays alive for 3
  seconds, sends SIGTERM, and verifies clean exit within 5
  seconds. macOS and Windows CI verifies the `--web`
  dependencies link but does not run the smoke test.

## Native TLS

`axum-server` (with the `tls-rustls` feature) provides HTTPS.
Two CLI flags — `--web-tls-cert <PATH>` and `--web-tls-key
<PATH>` — activate it; clap's `requires =` enforces that both
are supplied together or neither. When TLS is active the startup
URL line prints `https://` and the server uses
`axum_server::bind_rustls(addr, RustlsConfig)` with a
`Handle::graceful_shutdown` shim driven by `SHUTDOWN_REQUESTED`,
keeping the graceful-shutdown semantics above intact. A
reference systemd unit at `examples/ryll-web.service` shows the
TLS-enabled invocation with an `EnvironmentFile` pattern.

Operator-facing TLS setup, cert recipes and the reverse-proxy
fallback are in the [`--web` operator guide](/components/ryll/web-frontend/).
