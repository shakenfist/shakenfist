# Phase 3: Video frame capture

## Overview

Implement the `frame()` stub method in `CaptureSession` to
encode display surface snapshots as H.264 video in an MP4
container. A video frame is emitted after each MARK message
(frame boundary), with real timestamps so the video plays
back at actual session speed.

## Design

### Encoding pipeline

```
Surface RGBA pixels (from app)
         │
         ▼
  RgbaSliceU8 (openh264)
         │
         ▼
  YUVBuffer::from_rgb_source()
         │
         ▼
  Encoder::encode() → NAL units
         │
         ▼
  Mp4Writer::write_sample()
         │
         ▼
  display.mp4
```

### When frames are emitted

The display channel sends `ChannelEvent::DisplayMark`
after each batch of draw_copy messages. The app's
`process_events()` handles this event. When capture is
active, it should call `capture.frame()` with the current
surface pixels.

This means the `frame()` call happens on the GUI thread
(in `process_events`), not on the display channel's tokio
task. The surface pixel data lives in `RyllApp.surfaces`.

### Encoder initialisation

The H.264 encoder needs to know the frame dimensions, but
we don't know them until the first surface is created. The
`VideoWriter` is therefore lazily initialised on the first
`frame()` call.

The first frame also produces SPS and PPS NAL units which
are needed for the MP4 AvcConfig. We encode the first
frame, extract SPS/PPS from the bitstream, configure the
MP4 track, then write the first sample.

### Timestamps

Use `Instant::now() - session_start` for each frame,
converted to milliseconds. The MP4 timescale is set to
1000 (millisecond precision). Duration of each sample is
computed as the difference between the current and previous
frame timestamps.

### Surface selection

Only surface 0 (the primary surface) is captured. If
`frame()` is called with a different surface_id, it is
ignored with a debug log.

## Implementation steps

### Step 1: Add dependencies

In `Cargo.toml`:

```toml
openh264 = "0.6"
mp4 = "0.14"
bytes = "1"  # already present
```

### Step 2: Add VideoWriter struct

In `src/capture.rs`, add:

```rust
struct VideoWriter {
    encoder: openh264::encoder::Encoder,
    mp4_writer: mp4::Mp4Writer<BufWriter<File>>,
    track_id: u32,
    width: u32,
    height: u32,
    frame_count: u64,
    last_timestamp_ms: u64,
}
```

Wrapped in `Option<Mutex<VideoWriter>>` on
`CaptureSession` — `None` until first `frame()` call.

### Step 3: Lazy initialisation in frame()

On the first call to `frame()`:

1. Create `Encoder` with `EncoderConfig` sized to
   width x height.
2. Convert the first frame's RGBA pixels to YUV via
   `YUVBuffer::from_rgb_source()`.
3. Encode the first frame to get NAL units including
   SPS and PPS.
4. Extract SPS and PPS bytes from the encoded bitstream
   (the openh264 crate provides these via the encoder
   or NAL parsing).
5. Create `AvcConfig` with the SPS, PPS, and dimensions.
6. Create `Mp4Writer` with a video track configured from
   the `AvcConfig`.
7. Write the first encoded frame as sample 0.
8. Store everything in the `VideoWriter`.

### Step 4: Subsequent frame() calls

1. Convert RGBA to YUV.
2. Encode with `encoder.encode()`.
3. Compute timestamp_ms from `session_start.elapsed()`.
4. Compute duration_ms from `timestamp_ms - last_timestamp_ms`.
5. Create `Mp4Sample` with the NAL data, timestamp,
   duration, and `is_sync` flag (true for I-frames).
6. Write via `mp4_writer.write_sample()`.
7. Update `last_timestamp_ms` and `frame_count`.

### Step 5: Wire frame() calls from the app

In `app.rs`, in the `process_events()` match arm for
`ChannelEvent::DisplayMark`:

```rust
ChannelEvent::DisplayMark => {
    if let Some(ref capture) = self.capture {
        if let Some(surface) = self.surfaces.get(&0) {
            capture.frame(0, surface.pixels(), surface.width, surface.height);
        }
    }
}
```

This requires `DisplaySurface::pixels()` to be public,
which it already is (marked `#[allow(dead_code)]`).

### Step 6: Close and finalise

In `CaptureSession::close()`, if the video writer exists:

1. Call `mp4_writer.write_end()` to finalise the MP4
   container (writes the moov atom with sample tables).
2. Drop the encoder.
3. Log the frame count and file path.

Without calling `write_end()`, the MP4 file will be
corrupt (missing moov atom).

### Step 7: Handle dimension changes

If the surface dimensions change mid-session (e.g. VM
resolution change), log a warning and stop recording.
H.264 doesn't support mid-stream resolution changes
without a new encoder instance, and the MP4 track is
configured with fixed dimensions.

## Files to modify

| File | Changes |
|------|---------|
| `Cargo.toml` | Add `openh264` and `mp4` |
| `src/capture.rs` | VideoWriter, lazy init, frame encoding |
| `src/app.rs` | Call capture.frame() on DisplayMark |

## Fallback: PNG frames

If `openh264` or `mp4` prove problematic at runtime (e.g.
encoding errors, corrupt output), the fallback is to write
individual PNG frames using the `png` crate (pure Rust):

```
<DIR>/frames/frame-000001.png
<DIR>/frames/frame-000002.png
...
```

These can be assembled with:
```bash
ffmpeg -framerate 10 -i frame-%06d.png output.mp4
```

This fallback can be implemented alongside the MP4 path
and selected by the user or activated automatically on
encoder failure.

## Success criteria

- `--capture /tmp/test-cap` produces `display.mp4` that
  plays in VLC/mpv/browser.
- Video shows the SPICE display updates at correct timing.
- First frame shows the initial full-screen render.
- Subsequent frames show incremental updates composited
  onto the surface.
- No visible artifacts from YUV conversion.
- Without `--capture`, zero overhead.
- `pre-commit run --all-files` passes.
- Unit tests still pass.
