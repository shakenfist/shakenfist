# Phase 3: WebRTC plumbing (`webrtc-rs`)

## Prompt

Before responding to questions or making changes, explore the
codebase. Read the master plan at
`docs/plans/PLAN-web-frontend.md` (especially Resolutions §3
transport choice) and the Phase 1 + Phase 2 plans and their
execution histories. Key files for this phase:

- `shakenfist-spice-renderer/src/encoder/` — Phase 2's encoder.
  `EncoderTask::spawn(...) -> JoinHandle<Result<()>>` produces
  an `mpsc::Receiver<EncodedFrame>` of Annex-B-framed NALs at
  30 fps with keyframe-on-demand via
  `EncoderControl::RequestKeyframe`. Phase 3 wires this to a
  video RTP track.
- `shakenfist-spice-renderer/tests/webrtc_h264_smoke.rs` — the
  Phase 2 step 2e pre-flight that proved the `rtp` crate's
  `H264Payloader` accepts our NAL output (with the SPS/PPS →
  STAP-A bundling behaviour). Phase 3 inherits this fact.
- The existing extracted crate skeletons: `shakenfist-spice-{
  protocol, compression, usbredir, renderer}` for Cargo
  patterns. Phase 3 adds a fifth crate, `shakenfist-spice-webrtc`.
- `ryll/Cargo.toml` for how it consumes the existing extracted
  crates.

External references:

- `webrtc-rs` (crate name `webrtc`) is the umbrella crate for
  the Rust WebRTC stack. As of late 2025 / early 2026, current
  major version is 0.x with active churn; pin a specific
  version. The crate re-exports its sub-crates: `webrtc::api`,
  `webrtc::peer_connection`, `webrtc::track`, `webrtc::data_channel`,
  `webrtc::ice`, `webrtc::dtls`, `webrtc::media`, `webrtc::rtp`.
- RFC 6184 H.264 RTP payload format (verified workable by
  Phase 2 step 2e).
- RFC 7587 Opus RTP payload format (used in Phase 3d for the
  audio track).

Flag any uncertainty rather than guessing.

## Goal

Build the WebRTC plumbing that bridges a SPICE session (encoded
video, audio, input control) to a `RTCPeerConnection` ready to
expose to a browser in Phase 4. After this phase:

- New crate `shakenfist-spice-webrtc` ships at `version.workspace`
  alongside the other four extracted crates. Path + version
  dual-spec dep added to `ryll/Cargo.toml` (not yet consumed by
  ryll's binary; consumption lands in Phase 4).
- A `WebrtcBridge` struct wraps an `RTCPeerConnection`, exposes
  `new(config) -> Result<Self>`, `accept_offer(sdp) -> Result<sdp>`,
  and lifecycle hooks for video / audio / datachannel.
- Video track wiring: bridge consumes `mpsc::Receiver<EncodedFrame>`
  from `EncoderTask`, strips Annex-B start codes, payloads via
  `H264Payloader`, and writes RTP packets to a
  `TrackLocalStaticRTP`. Keyframe-on-attach signal: when a new
  `RTCPeerConnection` reaches `Connected`, the bridge sends
  `EncoderControl::RequestKeyframe` so the next frame is an IDR.
- Audio track wiring: synthetic Opus stream (real Opus
  passthrough lives in Phase 5). The bridge owns an audio
  `TrackLocalStaticRTP` and a small driver that emits constant
  silence (or a 440 Hz sine) to verify the audio path
  negotiates and packets flow.
- Datachannel wiring: a single ordered, reliable "control"
  datachannel for inputs (Phase 5) and cursor overlay (Phase
  5b). Phase 3 implements ping/pong round-tripping; payloads
  are out of scope until Phase 5.
- A loopback integration test creates two
  `RTCPeerConnection`s in-process, exchanges SDP directly
  (no signalling server), starts the encoder + audio + control
  flows, and asserts: video RTP packets arrive on the receiver,
  audio RTP packets arrive, datachannel ping/pong round-trips.

No HTTP signalling. No browser shell. No real Opus from the
SPICE playback channel. Those land in Phase 4 (HTTP/server) and
Phase 5 (production wiring).

## Scope

In:

- New `shakenfist-spice-webrtc` crate.
- Adding `webrtc = "0.x"` (umbrella crate, pinned) as the
  dep. Keep it pinned to a specific minor version to insulate
  against the ecosystem's churn.
- `WebrtcBridge` struct + lifecycle: new, accept_offer,
  on_connected, close.
- Video track: `TrackLocalStaticRTP` with H.264 payload type,
  driver that consumes `mpsc::Receiver<EncodedFrame>`.
- Audio track: `TrackLocalStaticRTP` with Opus payload type,
  synthetic-source driver.
- Datachannel: single "control" channel, ordered + reliable,
  ping/pong test.
- Keyframe-on-attach: bridge holds the `EncoderControl` sender
  and signals `RequestKeyframe` on PC connection state
  transitions to `Connected` (and on RTCP PLI receipt — see
  *Approach*).
- Loopback integration test that asserts the full pipeline
  works in-process, no browser involved.

Out:

- HTTP signalling endpoint and SDP exchange over the wire —
  Phase 4.
- Browser shell — Phase 4.
- Real Opus passthrough from SPICE playback channel — Phase 5.
- Real input event marshalling over the datachannel — Phase 5.
- TLS / DTLS-SRTP cert pinning beyond what webrtc-rs does by
  default. Per Resolution §8 we ship plain HTTP signalling;
  WebRTC's media transport is always DTLS-SRTP, but the SDP
  fingerprint is exchanged over plain HTTP. This is fine for
  the LAN-only threat model.
- TURN configuration. STUN-only (the LAN-only assumption from
  Resolution §3 holds).
- Multiple simultaneous viewers. MVP is single-viewer; the
  bridge handles one PC at a time.

## Approach

### Crate vs renderer-module placement

A new crate `shakenfist-spice-webrtc` sitting alongside
`shakenfist-spice-{protocol, compression, usbredir, renderer}`.
Reasons:

- The renderer crate is intentionally a SPICE-substrate
  library; its current API is "channels, framebuffer,
  encoder, session orchestrator". WebRTC plumbing is a
  *delivery mechanism* tied to one specific frontend
  (browser). Putting it in the renderer would broaden the
  renderer's scope from "SPICE substrate" to "SPICE
  substrate + a particular delivery mechanism".
- A third party building a SPICE → browser product would want
  the WebRTC bridge; a third party building a SPICE → native-
  desktop or SPICE → mobile product would not. The two
  audiences want different deps.
- The webrtc-rs dep tree is heavy (DTLS, SRTP, ICE, SCTP,
  STUN, ...). Keeping it in a separate crate lets ryll's
  GUI/headless build skip it entirely once feature gating
  lands (Future work in master plan).

### `WebrtcBridge` API shape

```rust
pub struct WebrtcBridge {
    pc: Arc<RTCPeerConnection>,
    video_track: Arc<TrackLocalStaticRTP>,
    audio_track: Arc<TrackLocalStaticRTP>,
    control_dc: Arc<RTCDataChannel>,
    encoder_control: mpsc::Sender<EncoderControl>,
    // Other internals: payloaders, RTP sequence + timestamp state.
}

pub struct WebrtcBridgeConfig {
    /// ICE servers (STUN). Empty by default; the LAN-only
    /// assumption means STUN is often unnecessary, but if the
    /// operator wants it, this is the knob.
    pub ice_servers: Vec<String>,
    /// Sender for `EncoderControl` so the bridge can request
    /// keyframes when a new PC attaches.
    pub encoder_control: mpsc::Sender<EncoderControl>,
}

impl WebrtcBridge {
    pub async fn new(config: WebrtcBridgeConfig) -> Result<Self> { ... }

    /// Accept an SDP offer (from the browser) and return our
    /// SDP answer. Wires the video / audio / datachannel
    /// transports as part of negotiation. Phase 4 will call
    /// this from the HTTP /offer handler.
    pub async fn accept_offer(&self, offer_sdp: String) -> Result<String> { ... }

    /// Spawn the video pump: consume EncodedFrame from `rx`,
    /// strip Annex-B start codes, payload via H264Payloader,
    /// write RTP packets to `self.video_track`. Returns a
    /// JoinHandle. Stops when `rx` is closed.
    pub fn spawn_video_pump(
        &self,
        rx: mpsc::Receiver<EncodedFrame>,
    ) -> tokio::task::JoinHandle<Result<()>> { ... }

    /// Spawn the audio pump (synthetic in Phase 3, real Opus
    /// in Phase 5). Phase 3 ships a `spawn_synthetic_audio_pump`
    /// that emits a 440 Hz sine encoded as Opus at 50 fps.
    pub fn spawn_synthetic_audio_pump(
        &self,
    ) -> tokio::task::JoinHandle<Result<()>> { ... }

    /// Send a control-channel message. Phase 5 will use this
    /// for input events and cursor overlay updates; Phase 3
    /// uses it only in tests.
    pub async fn send_control(&self, msg: &[u8]) -> Result<()> { ... }

    /// Subscribe to incoming control-channel messages.
    pub fn control_rx(&self) -> mpsc::Receiver<Vec<u8>> { ... }

    /// Cleanly close the bridge.
    pub async fn close(self) -> Result<()> { ... }
}
```

The `accept_offer` method is the single SDP entry point.
Internally it sets the remote description, generates the
answer, sets the local description, and waits for ICE
gathering to complete (so the operator can copy a
fully-resolved SDP — the LAN assumption means trickle ICE
isn't necessary for MVP; we can add it later).

### Video pump details

Consumes `EncodedFrame`s and writes RTP packets:

```rust
async fn run_video_pump(
    rx: mpsc::Receiver<EncodedFrame>,
    track: Arc<TrackLocalStaticRTP>,
    payloader: H264Payloader,
    clock_rate: u32,            // 90_000 for video
) -> Result<()> {
    let mut seq: u16 = rand::random();
    let mut frame_count: u64 = 0;
    while let Some(frame) = rx.recv().await {
        // Convert wall-clock timestamp_us to RTP timestamp:
        // rtp_ts = (timestamp_us * clock_rate) / 1_000_000
        let rtp_ts = ((frame.timestamp_us as u128) * (clock_rate as u128)
            / 1_000_000u128) as u32;

        for annex_b_nal in &frame.nal_units {
            // Strip the 4-byte Annex-B start code. (Phase 2
            // step 2b normalises every NAL to a 4-byte start
            // code prefix.)
            let raw_nal = &annex_b_nal[4..];
            let payloads = payloader.payload(MTU, &Bytes::copy_from_slice(raw_nal))?;
            // Phase 2 step 2e established that SPS/PPS produce
            // 0 payloads (cached and bundled into a STAP-A on
            // the next non-parameter NAL). That's fine; we
            // just skip empty payload sets.
            for payload in payloads {
                let pkt = rtp::packet::Packet {
                    header: rtp::header::Header {
                        version: 2,
                        padding: false,
                        extension: false,
                        marker: /* set on last packet of a frame */,
                        payload_type: H264_PAYLOAD_TYPE,
                        sequence_number: seq,
                        timestamp: rtp_ts,
                        ssrc: VIDEO_SSRC,
                        ..Default::default()
                    },
                    payload,
                };
                seq = seq.wrapping_add(1);
                track.write_rtp(&pkt).await?;
            }
        }
        frame_count += 1;
    }
    Ok(())
}
```

The `marker` bit on the last RTP packet of a frame is
important for receiver pacing — the decoder uses it to know
when a frame is complete. The pump needs to set `marker =
true` on the last packet of each frame's NALs. Easiest way:
collect all packets for one frame first, then iterate and set
`marker` on the last one before sending.

`H264_PAYLOAD_TYPE` and `VIDEO_SSRC` values: pick consistent
defaults. The webrtc-rs `MediaEngine` has helpers to register
codec-to-payload-type mappings; use those rather than hardcoding.

### Audio pump (synthetic) details

Phase 3's audio pump generates a 440 Hz sine wave at 48 kHz,
encodes via `audiopus` (or `opus` crate), and writes Opus RTP
packets at 50 fps (20 ms per packet, the Opus default). This
verifies the audio track negotiates and packets flow.

Phase 5 replaces this with a real Opus passthrough from
SPICE's playback channel — which is exactly the same
`Arc<TrackLocalStaticRTP>` consumer, just with a different
upstream source. So the synthetic pump's design is "close
enough to the production interface that swapping the source
later is mechanical".

### Datachannel details

Single ordered + reliable channel labelled "control". MTU
defaults to 16 KB (browser default; webrtc-rs follows). Phase
3 round-trips ping/pong as a smoke test; Phase 5 carries real
payloads (input events, cursor overlay updates).

The `RTCDataChannel::on_message` callback feeds an
`mpsc::Sender<Vec<u8>>` so the consumer can `recv` messages
async-style. `send_control(&[u8])` calls
`RTCDataChannel::send`.

### Keyframe-on-attach signal

Two triggers:

1. **PC connection state transition to `Connected`**: register
   `pc.on_peer_connection_state_change(...)`. When state
   becomes `Connected`, send `EncoderControl::RequestKeyframe`.
2. **RTCP PLI (Picture Loss Indication)**: webrtc-rs delivers
   incoming RTCP via `pc.on_rtcp_packet` (or similar — verify
   API). When a PLI arrives, send `EncoderControl::RequestKeyframe`.

Both go through the same `encoder_control: mpsc::Sender<EncoderControl>`
that the bridge holds. The receiver was already wired up by
the `EncoderTask::spawn(..., control_rx, ...)` call in Phase 2.

For Phase 3 the connect-transition signal is enough to verify
in tests; the PLI handler can be stubbed (just register the
callback so we know where it goes; emit a TODO comment if the
RTCP plumbing in webrtc-rs isn't easy to reach without more
groundwork).

### In-process loopback integration test

The test creates **two** `RTCPeerConnection`s in the same
process. One acts as the "server" (our bridge); the other
acts as a "browser stub" that consumes RTP and replies on
the datachannel.

Flow:

1. Server: `WebrtcBridge::new(config).await?` — creates PC1.
2. Browser stub: another PC2 with a video track receiver, an
   audio track receiver, and a datachannel handler.
3. Browser stub: `pc2.create_offer(...)` → SDP offer.
4. Server: `bridge.accept_offer(offer_sdp).await?` → SDP answer.
5. Browser stub: `pc2.set_remote_description(answer)`.
6. Drive ICE: both PCs are in-process; ICE candidates trickle
   between them via direct callback wiring (no STUN, no real
   network).
7. Server: `bridge.spawn_video_pump(encoder_rx)` and
   `bridge.spawn_synthetic_audio_pump()`.
8. Server: send a ping over the control DC.
9. Browser stub: assert it received N video RTP packets, M
   audio RTP packets, and the ping; reply with a pong.
10. Server: assert pong received.
11. Both close cleanly.

This test exercises the end-to-end flow without any network or
browser. Latency is sub-millisecond (everything's in-process).
It's the right shape for CI.

### RTP timestamp clock

Video clock rate is 90 kHz (RFC 4566 default for H.264). Audio
clock rate is 48 kHz (Opus). The bridge derives RTP timestamps
from `EncodedFrame::timestamp_us` (video) and the audio pump's
sample counter (audio). Since both originate from a wall clock
or sample-accurate counter, A/V sync is preserved as long as
the encoder's `timestamp_us` is accurate — which it is,
because Phase 2 step 2c propagates `FrameRef::timestamp_us`
verbatim onto `EncodedFrame::timestamp_us`.

### webrtc-rs version pinning

Phase 2 step 2e used `rtp = "0.17.1"` directly. The umbrella
`webrtc` crate version that re-exports `rtp = 0.17` should be
the matching choice. Verify on crates.io before pinning.

The webrtc-rs ecosystem has had API churn between minor
versions; pin precisely (e.g. `webrtc = "0.13"` not `"0.x"`).
If the version pinned for Phase 3 differs from the `rtp` 0.17
used in Phase 2's pre-flight, that's a planning bug worth
surfacing.

## Prerequisites

- Phase 2 complete. (It is.)
- thought-bubble rebased onto current develop. (It is.)

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a   | low    | sonnet | none | Scaffold the `shakenfist-spice-webrtc` crate. Mirror `shakenfist-spice-protocol/Cargo.toml` for structure. Deps: `webrtc = "0.x"` (verify the latest minor that re-exports `rtp = 0.17`; the `rtp = "0.17.1"` we used in Phase 2 step 2e gives the constraint); `tokio` (with `full`); `anyhow`; `thiserror`; `tracing`; `bytes`; `rand`; plus `path + version` deps on `shakenfist-spice-renderer` (for `EncoderControl`, `EncodedFrame`, possibly `FrameSource` for the test harness). Add the crate to the workspace `Cargo.toml` `members` list. Add `path + version` dep entry in `ryll/Cargo.toml` (placed alphabetically; not yet imported in ryll's source). Add `shakenfist-spice-webrtc/src/lib.rs` with just a placeholder doc comment. No code yet. Run `make build`, `make lint`, `make test`, `pre-commit run --all-files`. Single commit. |
| 3b   | high   | opus  | worktree | Implement `WebrtcBridge::new` and `WebrtcBridge::accept_offer`. The bridge owns an `Arc<RTCPeerConnection>`, three `Arc<TrackLocalStaticRTP>` fields (video, audio, but the audio track is a placeholder until 3d), an `Arc<RTCDataChannel>` (control), and a `mpsc::Sender<EncoderControl>`. `new` constructs the PC via webrtc-rs's `APIBuilder` + `MediaEngine` pattern, registers H.264 (90 kHz) and Opus (48 kHz) codecs, creates the video and audio tracks (`TrackLocalStaticRTP::new(...)`), adds them to the PC via `pc.add_track(...)`, creates the control DC, and registers the connection-state-change handler that sends `EncoderControl::RequestKeyframe` on `Connected`. `accept_offer` does the SDP dance: `set_remote_description(offer)` → `create_answer` → `set_local_description(answer)` → wait for ICE gathering complete → return the answer SDP. No video / audio pumps yet — those are 3c / 3d. Worktree because the API surface here is the largest unknown in Phase 3; if the chosen `webrtc` crate version's `MediaEngine`/`APIBuilder` shape differs from expectations, we want to throw the worktree away cleanly. Add at least one unit test that constructs `WebrtcBridge::new` with a stub `EncoderControl` channel and asserts no errors. Single commit. |
| 3c   | high   | opus  | worktree | Implement the video pump + keyframe-on-attach completion. Add `WebrtcBridge::spawn_video_pump(rx: mpsc::Receiver<EncodedFrame>) -> JoinHandle<Result<()>>`. Inside: own an `H264Payloader` (from `rtp::codecs::h264`), iterate `EncodedFrame`s, derive RTP timestamps from `frame.timestamp_us` at 90 kHz, strip Annex-B start codes from each NAL, payload via `H264Payloader::payload(MTU, raw_nal)`, set the marker bit on the last RTP packet of each frame, and `track.write_rtp(&pkt).await`. Sequence-number rollover handled via `wrapping_add`. SSRC: pick a constant or random; `rand::random::<u32>()` at bridge construction is fine. MTU: 1200 (typical for browser-side path MTU minus UDP+IP+SRTP overhead). Add a test that uses an in-process `EncoderTask` driven by the existing `SyntheticFrameSource` (from the renderer crate) feeding `spawn_video_pump`; assert the pump runs without errors for ~50 frames. Worktree. Single commit. |
| 3d   | medium | opus  | worktree | Add a synthetic Opus audio pump. Add `audiopus = "0.2"` (or `opus = "0.3"` — verify what's idiomatic) as a webrtc-crate dep. `WebrtcBridge::spawn_synthetic_audio_pump()` returns a `JoinHandle`. Inside: generate a 440 Hz sine at 48 kHz mono, encode via Opus in 20 ms windows (960 samples per frame), payload with `OpusPayloader` (from `rtp::codecs::opus`), set Opus payload type and 48 kHz timestamp, and `audio_track.write_rtp(&pkt).await` at 50 fps. Add a test that asserts the pump emits ~50 packets in ~1 second. The PR plan calls out that real Opus passthrough is Phase 5; Phase 3 just verifies the audio path negotiates and packets flow. Worktree. Single commit. |
| 3e   | medium | sonnet | none | Implement datachannel send/recv. The control DC is created in 3b. Add `WebrtcBridge::send_control(&[u8])` that calls `dc.send(&Bytes::copy_from_slice(msg))`. Wire `dc.on_message(...)` to push received messages onto an internal `mpsc::Sender<Vec<u8>>`; expose the receiver via `WebrtcBridge::control_rx() -> mpsc::Receiver<Vec<u8>>`. Add a test that uses two in-process `WebrtcBridge`-style PCs (or just a server bridge + a manual client PC for testing) to round-trip "ping" / "pong" over the DC. Single commit. |
| 3f   | high   | opus  | worktree | The full loopback integration test in `shakenfist-spice-webrtc/tests/loopback.rs`. Two `RTCPeerConnection`s in-process: server-side `WebrtcBridge` and a browser-stub PC that consumes video/audio tracks and the DC. Drive SDP offer/answer manually (no signalling server). Wire the encoder via `EncoderTask::spawn(H264Encoder::new(64, 64).unwrap(), SyntheticFrameSource::new(64, 64), ..., 30)` feeding the bridge's video pump. Spawn the synthetic audio pump. Send a "ping" on the DC; the browser stub replies "pong". Assertions: ≥10 video RTP packets received, ≥5 audio RTP packets received, pong received within 2 seconds. Run for ~1 second total. The test demonstrates the bridge's full surface area without any browser. Worktree because cross-PC ICE wiring for in-process loopback can be fiddly — webrtc-rs has helpers but the exact pattern needs experimentation. Single commit. |

After 3f, the `shakenfist-spice-webrtc` crate is the
single integration point Phase 4 will consume from a
ryll-side HTTP signalling endpoint.

## Step details

### Step 3b expanded brief

webrtc-rs's `APIBuilder` pattern is roughly:

```rust
use webrtc::api::APIBuilder;
use webrtc::api::media_engine::MediaEngine;
use webrtc::api::interceptor_registry::register_default_interceptors;
use webrtc::interceptor::registry::Registry;

let mut media_engine = MediaEngine::default();
media_engine.register_default_codecs()?;  // includes H.264 + Opus

let mut registry = Registry::new();
registry = register_default_interceptors(registry, &mut media_engine)?;

let api = APIBuilder::new()
    .with_media_engine(media_engine)
    .with_interceptor_registry(registry)
    .build();

let config = RTCConfiguration {
    ice_servers: vec![],  // or with STUN servers from config
    ..Default::default()
};
let pc = Arc::new(api.new_peer_connection(config).await?);
```

Confirm against the version's docs / examples. The
`register_default_codecs` + `register_default_interceptors`
pair is the canonical setup.

Track creation:

```rust
use webrtc::track::track_local::track_local_static_rtp::TrackLocalStaticRTP;
use webrtc::rtp_transceiver::rtp_codec::{RTCRtpCodecCapability, RTPCodecType};

let video_track = Arc::new(TrackLocalStaticRTP::new(
    RTCRtpCodecCapability {
        mime_type: webrtc::api::media_engine::MIME_TYPE_H264.to_owned(),
        ..Default::default()
    },
    "video".to_owned(),
    "ryll-spice".to_owned(),
));
let _ = pc.add_track(video_track.clone()).await?;

let audio_track = Arc::new(TrackLocalStaticRTP::new(
    RTCRtpCodecCapability {
        mime_type: webrtc::api::media_engine::MIME_TYPE_OPUS.to_owned(),
        ..Default::default()
    },
    "audio".to_owned(),
    "ryll-spice".to_owned(),
));
let _ = pc.add_track(audio_track.clone()).await?;
```

Datachannel:

```rust
use webrtc::data_channel::data_channel_init::RTCDataChannelInit;

let control_dc = pc.create_data_channel(
    "control",
    Some(RTCDataChannelInit {
        ordered: Some(true),
        max_retransmits: None,  // reliable
        ..Default::default()
    }),
).await?;
```

Connection-state handler:

```rust
let encoder_control = config.encoder_control.clone();
pc.on_peer_connection_state_change(Box::new(move |state: RTCPeerConnectionState| {
    let encoder_control = encoder_control.clone();
    Box::pin(async move {
        if state == RTCPeerConnectionState::Connected {
            let _ = encoder_control.send(EncoderControl::RequestKeyframe).await;
        }
    })
}));
```

If the `webrtc` version's API differs (different state enum
name, different callback signature), adapt — the goal is "on
PC-connected, request a keyframe". Don't fight the API.

### Step 3c expanded brief

The marker-bit logic matters. Per RFC 6184 §5.1, the marker
bit is set on the last RTP packet of an access unit (the last
packet that carries the last NAL of a frame). Implementation
shape:

```rust
let mut all_packets: Vec<rtp::packet::Packet> = Vec::new();
for annex_b_nal in &frame.nal_units {
    let raw_nal = &annex_b_nal[4..];
    let payloads = payloader.payload(MTU, &Bytes::copy_from_slice(raw_nal))?;
    // SPS/PPS produce empty payload sets per RFC 6184 — skip cleanly.
    for payload in payloads {
        all_packets.push(rtp::packet::Packet {
            header: rtp::header::Header {
                version: 2,
                payload_type: H264_PT,
                sequence_number: seq, // wrapping_add inside the loop
                timestamp: rtp_ts,
                ssrc: VIDEO_SSRC,
                marker: false, // set later
                ..Default::default()
            },
            payload,
        });
        seq = seq.wrapping_add(1);
    }
}
if let Some(last) = all_packets.last_mut() {
    last.header.marker = true;
}
for pkt in all_packets {
    track.write_rtp(&pkt).await?;
}
```

The `H264_PT` payload type number is conventionally 102 or 96
for H.264 in dynamic SDP; `register_default_codecs` picks one
internally and the codec capability advertises it via SDP.
The `MediaEngine` API has a way to fetch the negotiated
payload type — use that rather than hardcoding.

### Step 3f expanded brief

In-process loopback wiring. The trick: webrtc-rs PCs need a
way to exchange ICE candidates. Without a network the
candidates would normally be obtained via STUN; for
in-process loopback we either:

1. Set `RTCConfiguration::ice_servers = vec![]` (no STUN) and
   rely on host candidates only — should work since both PCs
   are on the same host with the same loopback interface.
2. Manually trickle: `pc1.on_ice_candidate(|c| pc2.add_ice_candidate(c))`
   and vice versa.

Try option 1 first; fall back to option 2 if the PCs don't
finish ICE within ~5 seconds. Either way, the test should
have a timeout that aborts cleanly so it doesn't hang CI.

The browser-stub PC needs to receive video / audio tracks
and a datachannel:

```rust
let stub_pc = api.new_peer_connection(config).await?;

let video_received = Arc::new(AtomicUsize::new(0));
let video_received_c = video_received.clone();
stub_pc.on_track(Box::new(move |track, _, _| {
    let video_received = video_received_c.clone();
    Box::pin(async move {
        loop {
            match track.read_rtp().await {
                Ok((_pkt, _attrs)) => { video_received.fetch_add(1, Ordering::Relaxed); }
                Err(_) => break,
            }
        }
    })
}));

stub_pc.on_data_channel(Box::new(|dc| {
    Box::pin(async move {
        let dc_clone = dc.clone();
        dc.on_message(Box::new(move |msg| {
            let dc = dc_clone.clone();
            Box::pin(async move {
                if msg.data == "ping" {
                    let _ = dc.send_text("pong".to_owned()).await;
                }
            })
        }));
    })
}));
```

The `on_track` callback distinguishes video vs audio via the
track's RTP codec parameters (kind=video / kind=audio).

## Acceptance criteria

- `make lint` passes after each of 3a, 3b, 3c, 3d, 3e, 3f.
- `make test` passes after each step.
- After 3f: the loopback test runs to completion in <5
  seconds and asserts video + audio + datachannel
  round-tripping all worked.
- `pre-commit run --all-files` passes.
- `shakenfist-spice-webrtc/src/` has zero dependencies on
  `eframe`, `egui`, `ryll`. Verify with grep.
- Each of 3a–3f is a single commit on `thought-bubble`.

## Risks

- **webrtc-rs version churn.** The umbrella `webrtc` crate
  has had API breaks between 0.x minors. Pin precisely.
  Verify the chosen version's `rtp` re-export matches the
  `rtp = 0.17.1` Phase 2 step 2e used. If it doesn't, there's
  a coordination issue worth flagging early.
- **Loopback ICE.** In-process `RTCPeerConnection` pairs may
  surprise on ICE — webrtc-rs's ICE agent assumes real
  network behaviour. The test in 3f has a hard timeout to
  prevent hangs; if it fails, switch to manual candidate
  trickle.
- **Marker-bit correctness.** Decoders are tolerant of
  missing marker bits but receivers may delay frame display
  waiting for the marker. Get this right in 3c and verify in
  the loopback test (the receiving side's
  `track.read_rtp` returns packets; we can assert the marker
  bit is set on the expected packets).
- **Synthetic audio in tests.** The 50 fps cadence with
  short Opus frames may exhibit timing flakiness in CI under
  load. If the loopback test asserts ≥5 audio packets in 1
  second and only gets 3, widen the floor.
- **`MediaEngine` codec registration.**
  `register_default_codecs` may not advertise H.264 by
  default depending on webrtc-rs build features (some builds
  exclude H.264 if the encoder/decoder isn't compiled in).
  If the SDP doesn't include H.264 after default
  registration, register it manually with explicit codec
  parameters.
- **DTLS cert generation.** webrtc-rs generates self-signed
  DTLS certs by default; this is fine for MVP but rotation
  / persistence isn't addressed. Future work.

## Documentation updates

After 3f, update:

- `ARCHITECTURE.md` — add the WebRTC bridge to the data flow
  diagram. Note that ryll's web mode (Phase 4+) consumes the
  bridge via the renderer's encoder output.
- `AGENTS.md` — note the new crate and its purpose.
- `docs/multi-mode-parity.md` — no change yet; the bridge
  isn't user-visible until Phase 4 wires HTTP signalling.
- `docs/plans/PLAN-web-frontend.md` — flip Phase 3 row to
  "In progress" on first phase commit, "Complete" on 3f.
- `docs/plans/index.md` — Web frontend status line gets
  Phase 3 marked complete when 3f lands.

These doc updates can be batched into the 3f commit, or
done as a follow-up commit on top of 3f.

## Estimated total scope

Roughly 1500–2000 lines of new code across six commits, the
bulk in 3b (PC + track setup, ~400 lines), 3c (video pump,
~200 lines), and 3f (integration test, ~400 lines). 3a is
scaffolding (~50 lines), 3d ~150 lines, 3e ~100 lines.

## Back brief

Before executing 3a, the implementing agent should
back-brief: which `webrtc` crate version got picked, which
`rtp` version it re-exports, and whether that matches the
`rtp = 0.17.1` Phase 2 step 2e used.

Subsequent steps (3b–3f) follow the same pattern: back-brief
first, edit second.
