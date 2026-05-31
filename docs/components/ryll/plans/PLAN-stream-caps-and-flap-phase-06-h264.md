# Phase 6 — Multi-codec + H.264

Phase 6 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the master
plan, the phase 3 plan (which established the per-platform
decoder pattern), and the relevant source files: especially
`shakenfist-spice-renderer/src/channels/display.rs` (the
STREAM_CREATE / STREAM_DATA dispatch is around line 1267 /
1335 and the MJPEG decode is at 1450–1536), the existing
`encoder/h264.rs` for openh264 wrapping precedent, and
`shakenfist-spice-protocol/src/constants.rs` for the display
caps and where to add the new ones. Ground answers in what
the code does today.

For the `openh264` crate, read the installed source in
`~/.cargo/registry/src/index.crates.io-*/openh264-0.9.3/src/`.
The decoder API is `Decoder::decode(packet) ->
Result<Option<DecodedYUV>>`; `DecodedYUV::write_rgba8(buf)`
writes the converted RGBA into a caller-owned buffer.

Flag uncertainty explicitly rather than guessing.

## Goal

Decode H.264 video streams alongside MJPEG so the SPICE
server's modern codec preferences are honoured. Server-side
SPICE has supported H.264 streaming for years; spice-gtk
clients have accepted it for years; ryll currently rejects
anything that isn't MJPEG (display.rs:1530-1536: "unsupported
codec {codec_type}"). Once we advertise MULTI_CODEC + CODEC_H264,
the server is free to pick H.264 for video streams — which is
typically smaller on the wire than MJPEG and the right answer
for sustained playback.

Phase 3 lifted MJPEG decode to platform-native paths (ImageIO /
WIC / VA-API / libjpeg-turbo). Phase 6 introduces a second
video codec with similar care: a clean `VideoDecoder` trait
that absorbs the per-stream state H.264 requires (codec context,
reference frames, SPS/PPS), with `openh264` as the software
backend. Hardware H.264 decoders (VideoToolbox on macOS,
MediaFoundation on Windows, VA-API on Linux) are deliberately
out of scope here — they would be a follow-up phase mirroring
phase 3's per-platform JPEG decoder work, after we've proven
the dispatch shape with the software path first.

## Scope

In scope:

- New `VideoDecoder` trait + `MJpegVideoDecoder` /
  `H264VideoDecoder` impls in a new module
  `shakenfist-spice-renderer/src/channels/video_decoder.rs`
  (or in `shakenfist-spice-compression` if we want it next
  to the JPEG decoder — see Q3 below).
- Per-stream `VideoDecoder` instance owned by `StreamState`,
  selected at `STREAM_CREATE` based on `codec_type`.
- `H264VideoDecoder` wraps `openh264::decoder::Decoder`,
  feeds raw NAL bytes, converts YUV to RGBA via
  `DecodedYUV::write_rgba8`.
- Refactor the MJPEG STREAM_DATA path
  (`display.rs:1450-1536`) so the dispatch becomes
  `stream.video_decoder.decode(...)` regardless of codec.
  MJPEG-specific bits (DHT extraction/caching) move into
  `MJpegVideoDecoder`.
- Advertise the new SPICE display capabilities in
  `DEFAULT_DISPLAY` (`shakenfist-spice-protocol/src/constants.rs`):
  - `DISPLAY_MULTI_CODEC` (bit 8)
  - `DISPLAY_CODEC_MJPEG` (bit 9)
  - `DISPLAY_CODEC_H264` (bit 11)
- Per-stream snapshot field `video_decoder_backend: String`
  exposing the active decoder's name (`"MJPEG (ImageIO)"`,
  `"H264 (openh264)"`, etc.) — replaces the existing
  `mjpeg_decoder_backend` field which becomes the MJPEG
  case of the more general field.
- Aggregate counters on `DisplaySnapshot`:
  `h264_decode_recent_min/max/mean_us`,
  `h264_decode_total_count`, `h264_decode_failed_count`
  — same shape as the existing mjpeg_decode_recent_*
  fields from phase 3F.
- Per-platform manual smoke test as step 6F.

Out of scope:

- Hardware H.264 decoders (VideoToolbox / MediaFoundation /
  VA-API). Software-only via openh264 ships first. Hardware
  decoders become a separate phase analogous to phase 3,
  once we've confirmed the dispatch architecture is right.
- VP8 / VP9 / H.265 codecs. The SPICE protocol allocates
  caps 10 (VP8), 13 (VP9), 14 (H265). We advertise none
  of them in this phase; H.264 covers the bulk of real
  server deployments and is the obvious first add.
- Offloading H.264 decode to a separate task. Keep inline
  on the channel task to match MJPEG. If profiling later
  shows the channel task is starved, move to a worker
  pool — but that's a separate optimisation.
- Display channel restructure beyond the per-stream
  decoder field. The existing STREAM_CREATE / STREAM_DATA
  handlers stay where they are; only the per-codec dispatch
  changes.
- Render-side YUV (skipping the YUV → RGBA convert and
  pushing YUV directly to the GPU). Would be a meaningful
  win but requires changes in the renderer/wgpu path — a
  separate phase if anyone wants to chase the last 5-10 ms
  per frame.

## Open questions

- **Q1 (decide in 6A): trait shape — stateful per-stream
  or stateless per-frame?** MJPEG is stateless per frame
  (each JPEG carries all its tables, or we inject the cached
  DHT). H.264 is stateful (decoder maintains reference
  frame buffer; SPS/PPS arrive at stream start and persist).
  Working proposal: trait takes `&mut self` and is
  instantiated once per `StreamState` at `STREAM_CREATE`.
  Stateless decoders just don't maintain state. The trait:
  ```rust
  pub trait VideoDecoder: Send {
      fn decode(&mut self, packet: &[u8])
          -> Result<Option<DecodedFrame>, VideoDecoderError>;
      fn name(&self) -> &'static str;
  }
  pub struct DecodedFrame {
      pub rgba: Vec<u8>,
      pub width: u32,
      pub height: u32,
  }
  ```
  Mirrors `JpegDecoder` from phase 3 but stateful.

- **Q2 (decide in 6A): how do we plumb the existing
  ImageIO/WIC/VA-API/mozjpeg MJPEG decoders through the
  new trait?** `MJpegVideoDecoder` could either own its
  own `Arc<dyn JpegDecoder>` (the one from phase 3's
  `best_for_platform()`) or be constructed with one.
  Recommend: construct with one — the `DisplayChannel`
  already holds an `Arc<dyn JpegDecoder>`, just hand a
  clone to each new `MJpegVideoDecoder`. Avoids global
  state, keeps phase 3's per-platform selection unchanged.

- **Q3 (decide in 6A): where does the module live?**
  Options:
  - `shakenfist-spice-renderer/src/channels/video_decoder.rs`
    — close to the dispatch site (display.rs), small
    module.
  - `shakenfist-spice-compression/src/video.rs` —
    consistent with where the JPEG decoder lives.
  - `shakenfist-spice-renderer/src/video.rs` —
    middle ground; renderer-level concept but not buried
    in `channels/`.
  Recommend `shakenfist-spice-compression/src/video.rs`
  — matches the JPEG decoder's home, makes it reusable
  by the webrtc/encoder side if anyone wants the symmetry
  later, and the `openh264` crate dep already lives close
  by.

- **Q4 (decide in 6B): NAL framing — do we pass the raw
  STREAM_DATA payload to openh264 or pre-parse?** SPICE's
  STREAM_DATA carries a complete H.264 access unit per
  message (one frame's worth of NAL units in
  Annex B framing — start codes `00 00 00 01` between
  NALUs). `openh264::Decoder::decode` accepts a single
  packet and expects either Annex B or AVCC framing.
  Recommend: assume Annex B (matches SPICE's wire
  convention per spice-protocol docs), pass the payload
  through unmodified. If decode fails on the first frame
  with an "invalid start code" error, the framing is
  wrong and we revisit. Document the assumption in code
  comments.

- **Q5 (decide in 6B): how do we handle decoder errors
  mid-stream?** Bump `frames_decode_failed` on
  `StreamState` (existing field), log at debug, do NOT
  tear the stream — the next IDR frame should resync.
  After ≥3 consecutive failures, log at warn (to surface
  persistent corruption without spamming). Don't fall
  back to MJPEG mid-stream — the server picked H.264, it
  doesn't switch mid-stream, and our cap advertisement
  already promised support.

- **Q6 (open): should H.264 decode happen on `spawn_blocking`
  even if "inline" is the baseline?** openh264 decode is
  CPU-bound (5-15 ms per 1080p frame). Inline blocks the
  display-channel tokio task for that duration. If the
  channel task is blocked, the next STREAM_DATA can't
  arrive, the rate limits, server may flap the stream.
  **Recommend: inline first**, mirror what MJPEG does;
  measure with the phase 4 decode-duration aggregates;
  move to `spawn_blocking` (per-stream task or worker
  pool) only if data shows we're starving the channel.
  Capture this as a follow-up.

## Design notes

### Trait

```rust
// shakenfist-spice-compression/src/video.rs (new module)

pub trait VideoDecoder: Send {
    fn decode(&mut self, packet: &[u8])
        -> Result<Option<DecodedFrame>, VideoDecoderError>;
    fn name(&self) -> &'static str;
}

pub struct DecodedFrame {
    pub rgba: Vec<u8>,
    pub width: u32,
    pub height: u32,
}

#[derive(Debug, thiserror::Error)]
pub enum VideoDecoderError {
    #[error("decoder failed: {0}")]
    Decode(String),
    #[error("unsupported codec_type {0}")]
    UnsupportedCodec(u8),
}

pub fn for_stream(
    codec_type: u8,
    jpeg_decoder: Arc<dyn JpegDecoder>,
) -> Result<Box<dyn VideoDecoder>, VideoDecoderError> {
    match codec_type {
        SPICE_VIDEO_CODEC_TYPE_MJPEG => Ok(Box::new(MJpegVideoDecoder::new(jpeg_decoder))),
        SPICE_VIDEO_CODEC_TYPE_H264 => Ok(Box::new(H264VideoDecoder::new()?)),
        other => Err(VideoDecoderError::UnsupportedCodec(other)),
    }
}
```

### MJpegVideoDecoder

Absorbs the DHT extract/inject logic and the existing
phase-3 `JpegDecoder` dispatch. Stateful only in the sense
that it holds the cached DHT bytes across frames.

```rust
pub struct MJpegVideoDecoder {
    inner: Arc<dyn JpegDecoder>,
    cached_dht: Option<Vec<u8>>,
}

impl VideoDecoder for MJpegVideoDecoder {
    fn decode(&mut self, packet: &[u8]) -> Result<Option<DecodedFrame>, VideoDecoderError> {
        // Same DHT extract/inject dance as today's display.rs:1460-1470.
        // Then self.inner.decode(...) and shape-convert to DecodedFrame.
    }
    fn name(&self) -> &'static str {
        // Phase 3 surfaced this via JpegDecoder::name() —
        // pass it through so a bug report still says
        // "ImageIO" / "WIC" / "libjpeg-turbo" / etc.
        self.inner.name()
    }
}
```

### H264VideoDecoder

```rust
pub struct H264VideoDecoder {
    decoder: openh264::decoder::Decoder,
    rgba_scratch: Vec<u8>,
}

impl H264VideoDecoder {
    pub fn new() -> Result<Self, VideoDecoderError> {
        let decoder = openh264::decoder::Decoder::new()
            .map_err(|e| VideoDecoderError::Decode(e.to_string()))?;
        Ok(Self { decoder, rgba_scratch: Vec::new() })
    }
}

impl VideoDecoder for H264VideoDecoder {
    fn decode(&mut self, packet: &[u8]) -> Result<Option<DecodedFrame>, VideoDecoderError> {
        let decoded = self.decoder.decode(packet)
            .map_err(|e| VideoDecoderError::Decode(e.to_string()))?;
        let Some(yuv) = decoded else { return Ok(None); };
        let (w, h) = yuv.dimensions();
        let buf_len = w * h * 4;
        if self.rgba_scratch.len() != buf_len {
            self.rgba_scratch.resize(buf_len, 0);
        }
        yuv.write_rgba8(&mut self.rgba_scratch);
        Ok(Some(DecodedFrame {
            rgba: self.rgba_scratch.clone(),
            width: w as u32,
            height: h as u32,
        }))
    }
    fn name(&self) -> &'static str {
        "H264 (openh264)"
    }
}
```

### StreamState changes

Add a boxed `VideoDecoder` field:

```rust
struct StreamState {
    // ... existing fields ...
    video_decoder: Box<dyn VideoDecoder>,
    // mjpeg_decoder_backend stays for backwards compat in
    // the snapshot; populated from video_decoder.name() at
    // STREAM_CREATE (becomes "MJPEG (ImageIO)" /
    // "H264 (openh264)" / etc.). Consider renaming to
    // video_decoder_backend in step 6D — see Q below.
}
```

### Dispatch refactor

Today (display.rs:1450-1536) the MJPEG arm is hardcoded. Replace
with a codec-agnostic dispatch:

```rust
let frame_data = preprocess_packet(stream, msg_type, payload);
let decode_start = Instant::now();
let result = stream.video_decoder.decode(frame_data);
let decode_duration_us = ...;

// Aggregate counters: keep MJPEG ones, add H.264 ones,
// dispatch on stream.codec_type. Or: switch the aggregate
// counters to be per-codec via two parallel rings on
// DisplayChannel.

match result {
    Ok(Some(DecodedFrame { rgba, width, height })) => {
        // existing ChannelEvent::ImageReady send
    }
    Ok(None) => { /* no frame this packet — H.264 sometimes
                     needs multiple packets per frame; bump
                     a "needs more data" counter */ }
    Err(VideoDecoderError::Decode(msg)) => {
        stream.frames_decode_failed += 1;
        debug!("display: stream {} decode failed: {}", stream_id, msg);
    }
    Err(VideoDecoderError::UnsupportedCodec(_)) => unreachable!(
        "video_decoder is set at STREAM_CREATE only for supported codecs"
    ),
}
```

### Capability advertisement

```rust
// shakenfist-spice-protocol/src/constants.rs
pub const DISPLAY_MULTI_CODEC: u32 = 1 << 8;
pub const DISPLAY_CODEC_MJPEG: u32 = 1 << 9;
pub const DISPLAY_CODEC_H264: u32 = 1 << 11;

pub const DEFAULT_DISPLAY: u32 = DISPLAY_SIZED_STREAM
    | DISPLAY_MONITORS_CONFIG
    | DISPLAY_COMPOSITE
    | DISPLAY_A8_SURFACE
    | DISPLAY_STREAM_REPORT
    | DISPLAY_LZ4_COMPRESSION
    | DISPLAY_MULTI_CODEC
    | DISPLAY_CODEC_MJPEG
    | DISPLAY_CODEC_H264;
```

Plus `SPICE_VIDEO_CODEC_TYPE_H264 = 3` near the existing
MJPEG constant in display.rs.

### Snapshot additions

`StreamSnapshot` — keep the existing `mjpeg_decoder_backend`
field for source-compat (the JSON consumers already exist).
Consider renaming to `video_decoder_backend` in step 6D if
the breaking change is OK. Working proposal: introduce
`video_decoder_backend: String` alongside, keep
`mjpeg_decoder_backend` for the MJPEG case (set to the
JpegDecoder name as today), `video_decoder_backend` always
set (`"MJPEG"` or `"H264 (openh264)"`). Decide in 6D
whether to deprecate the old field.

`DisplaySnapshot` aggregate fields:

```rust
pub h264_decode_recent_min_us: u32,
pub h264_decode_recent_max_us: u32,
pub h264_decode_recent_mean_us: u32,
pub h264_decode_total_count: u64,
pub h264_decode_failed_count: u64,
```

Same shape and naming convention as the phase 3F MJPEG
aggregates. The `update_snapshot` writes them from a
`h264_recent_durations` ring on `DisplayChannel`.

## Execution step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6A | medium | sonnet | none | **VideoDecoder trait + dispatch refactor (MJPEG only).** Create `shakenfist-spice-compression/src/video.rs` with the `VideoDecoder` trait, `DecodedFrame`, `VideoDecoderError`, `for_stream(codec_type, jpeg_decoder)` selector. Implement `MJpegVideoDecoder` only — pull the DHT extract/inject + `JpegDecoder::decode` chain from `display.rs:1450-1536` into the impl. Add `video_decoder: Box<dyn VideoDecoder>` to `StreamState`. In the `STREAM_CREATE` handler (display.rs:1267 area), call `video::for_stream(codec_type, self.jpeg_decoder.clone())` and store the result; if it returns `UnsupportedCodec`, log and skip the stream (preserving today's behaviour). Refactor the STREAM_DATA arm so it calls `stream.video_decoder.decode(packet)` regardless of codec. **Behaviour-preserving**: MJPEG path must still produce identical RGBA, identical decode timings (within noise), identical snapshot fields. Phase 4F's aggregate `mjpeg_decode_*` counters stay populated for the MJPEG branch — wire them from the dispatch path rather than from inside the trait impl. Verify `make build && make test && make lint`. No H.264 wiring yet. |
| 6B | high | opus | none | **H264VideoDecoder backend.** Implement `H264VideoDecoder` per *Design notes / H264VideoDecoder*. Read the openh264 crate source (already in `~/.cargo/registry/src/index.crates.io-*/openh264-0.9.3/src/decoder.rs`) for the exact `Decoder::new()` / `decode()` / `DecodedYUV` API; the sketch in the plan is illustrative but the implementer should match the actual crate signatures. Per Q4: assume Annex B framing on the STREAM_DATA payload and pass through unmodified. Per Q5: bump `frames_decode_failed` on errors, log at debug, escalate to warn after 3 consecutive failures (use a counter on the stream). Wire H264 into `for_stream()` selector keyed on `SPICE_VIDEO_CODEC_TYPE_H264 = 3`. Add `H264_decode_total_count` / failed / recent durations tracking parallel to the MJPEG aggregates (`DisplayChannel` ring + `update_snapshot` writes). Round-trip unit test: encode a small frame via `openh264::encoder::Encoder` (the codebase already uses this in `encoder/h264.rs`), decode via `H264VideoDecoder`, assert the RGBA is within JPEG-lossy tolerance (this is H.264 not JPEG but the principle is the same — lossy codec, per-channel diff threshold). **Why opus:** stateful decoder, fiddly NAL framing, the round-trip test must not get the encode/decode framing wrong (otherwise the test passes but real decode fails). |
| 6C | low | sonnet | none | **Cap advertisement.** Add `DISPLAY_MULTI_CODEC = 1 << 8`, `DISPLAY_CODEC_MJPEG = 1 << 9`, `DISPLAY_CODEC_H264 = 1 << 11` to `shakenfist-spice-protocol/src/constants.rs`. Add to `DEFAULT_DISPLAY`. Add `SPICE_VIDEO_CODEC_TYPE_H264 = 3` near the existing `SPICE_VIDEO_CODEC_TYPE_MJPEG` constant in `display.rs`. Add the new caps to `shakenfist-spice-protocol/src/logging.rs`' cap-name map so traffic-viewer output names them. Add a regression test in `logging.rs` for the new mappings. Verify `make build && make test && make lint`. |
| 6D | low | sonnet | none | **Per-stream + aggregate snapshot fields.** Add `video_decoder_backend: String` to `StreamSnapshot` (populated from `stream.video_decoder.name()` at `STREAM_CREATE`). Keep `mjpeg_decoder_backend` as-is for the MJPEG case (the existing JSON consumers already key on it; don't break them). Add `h264_decode_recent_min/max/mean_us`, `h264_decode_total_count`, `h264_decode_failed_count` to `DisplaySnapshot` matching the phase 3F shape. Wire `update_snapshot` writes. Extend `test_display_snapshot_serialises` to populate non-zero values for each new field and assert each appears in JSON. Verify `make build && make test && make lint`. |
| 6E | low | haiku | none | **Docs touch-up.** Update `ARCHITECTURE.md` if it lists supported codecs — note that H.264 streams are now decoded via openh264, alongside MJPEG. Add a one-sentence note to `AGENTS.md` about the openh264 runtime requirement (or lack thereof — `openh264-sys2` builds and links libopenh264 from source, no system dep). Update `docs/libvirt-spice-recommendations.md` `streaming-video` section to mention that with H.264 support shipped, sustained video over `streaming-video=filter` should be cheaper bandwidth-wise. Cross-link the phase-3 ImageIO/WIC/VA-API decoder doc with the new H.264 path. Verify `pre-commit run --all-files`. |
| 6F | — | — | — | **Per-platform smoke test.** Operator-driven. Run a ryll session against a real spice-server with `streaming-video=all` (or `filter`) on a guest that produces video that the server elects to stream. The server should now pick H.264 (it'll log `STREAM_CREATE codec=3`); confirm in the bug-report `StreamSnapshot.codec_type` is `3` and `video_decoder_backend` is `"H264 (openh264)"`. Confirm: (a) frames decode without errors (`frames_decode_failed = 0`); (b) `DisplaySnapshot.h264_decode_recent_mean_us` is reasonable (target: ≤30 ms at 1920×1080); (c) video plays smoothly. Compare bandwidth and CPU against a session that forces MJPEG (set `--client-caps disable-h264` if/when that flag exists, or just check the cap log against an older ryll). Verify on macOS and Linux at minimum; Windows if a runner is available. |

Commits: one per step (6A through 6E). 6F is operator verification.

## Test plan

Automated (added by 6A–6D):

- `VideoDecoder` trait + `for_stream` selector unit tests
  (6A): construct for MJPEG codec → name matches the
  underlying JpegDecoder's; construct for H264 codec → name
  is `"H264 (openh264)"`; construct for unknown codec →
  `UnsupportedCodec` error.
- MJPEG round-trip via `MJpegVideoDecoder` (6A): re-use
  phase 3B's mozjpeg round-trip with the new trait
  wrapper, assert behaviour-preservation.
- H.264 round-trip via `H264VideoDecoder` (6B): encode a
  16×16 or 64×64 RGBA pattern via `openh264::encoder::Encoder`,
  decode via `H264VideoDecoder`, assert RGBA within tolerance
  (per-channel max diff ≤ 30, mean ≤ 10 — H.264 is lossier
  than JPEG at low resolution).
- Cap logging regression (6C): assert the new cap bit
  numbers map to the right names.
- `test_display_snapshot_serialises` extended for the new
  `video_decoder_backend` per-stream field and the new
  `h264_decode_*` aggregate fields (6D).

Manual (6F):

- Smoke against `sf-4` (or whichever test target has
  H.264 in the server build). Confirm streams arrive with
  `codec_type=3`, decode without errors, mean decode
  duration within target, video plays smoothly.

## Documentation impact

`ARCHITECTURE.md` capability tables and decoder section
(6E). `AGENTS.md` mentions openh264 (no runtime dep but
worth noting the build pulls libopenh264 source). The
libvirt recommendations doc's `streaming-video=filter`
section gets a small update noting H.264 is now decoded
client-side, which makes the `filter` heuristic cheaper
overall. Phase 10 (docs) will further consolidate.

## Success criteria

- `shakenfist_spice_compression::video::for_stream(codec_type, jpeg_decoder)`
  exists and returns the right decoder per codec.
- `StreamState::video_decoder` is constructed at STREAM_CREATE
  and used for every STREAM_DATA decode.
- `DEFAULT_DISPLAY` advertises MULTI_CODEC + CODEC_MJPEG +
  CODEC_H264.
- `StreamSnapshot::video_decoder_backend` reflects the
  active decoder in bug reports.
- `DisplaySnapshot::h264_decode_recent_*` and totals
  appear in `channel-state.json`.
- On a real H.264-capable server, decoding works: mean
  `h264_decode_recent_mean_us` ≤ 30 ms at 1920×1080,
  `frames_decode_failed = 0` for a clean stream, video
  plays smoothly.
- `make build && make test && make lint && pre-commit run
  --all-files` clean.

## Back brief

Before executing 6A, the implementing sub-agent should
back-brief the operator with:

- Decision on Q3 (module location).
- Decision on whether to deprecate `mjpeg_decoder_backend`
  in favour of `video_decoder_backend` (Q5 in design notes).
- Confirmation that the MJPEG path is genuinely behaviour-
  preserving (snapshot fields match, decode timings match
  within noise).
