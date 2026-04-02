# Phase 1: Capture infrastructure

## Overview

Add the `--capture <DIR>` CLI flag and the `CaptureSession`
struct that phases 2 and 3 will use. This phase does not
write pcap or video — it establishes the plumbing so that
channels can call capture methods, and those methods are
no-ops until the later phases fill them in.

## Implementation steps

### Step 1: Add `--capture` CLI flag

In `config.rs`, add to `Args`:

```rust
/// Directory for protocol/display capture output
#[arg(long)]
pub capture: Option<String>,
```

### Step 2: Create `capture` module

New file `src/capture.rs` with:

```rust
pub struct CaptureSession {
    pub dir: PathBuf,
    pub start: Instant,
}
```

`CaptureSession::new(dir)` creates the directory (with
`fs::create_dir_all`) and records the start timestamp.

Stub methods for phases 2 and 3:

```rust
pub fn packet_sent(&self, channel: &str, data: &[u8]) {}
pub fn packet_received(&self, channel: &str, data: &[u8]) {}
pub fn frame(&self, surface_id: u32, pixels: &[u8],
             width: u32, height: u32) {}
pub fn close(&mut self) {}
```

### Step 3: Wire into main.rs

- If `args.capture` is `Some(dir)`, create a
  `CaptureSession` and wrap in `Arc<CaptureSession>`.
- Pass `Option<Arc<CaptureSession>>` to `run_gui` /
  `run_headless`, which pass it to `run_connection`.
- `run_connection` passes a clone of the `Arc` to each
  channel constructor.

### Step 4: Add capture to channel constructors

Each channel struct gets a new field:

```rust
capture: Option<Arc<CaptureSession>>,
```

In `send()`, call:
```rust
if let Some(ref c) = self.capture {
    c.packet_sent("channel_name", data);
}
```

In the read loop, after extending the buffer, call:
```rust
if let Some(ref c) = self.capture {
    c.packet_received("channel_name", &chunk[..n]);
}
```

These are no-ops for now (stub methods), but the wiring
is in place for phases 2 and 3.

### Step 5: Add capture call for display MARK

In the display channel's MARK handler, after sending
the `DisplayMark` event, call:

```rust
if let Some(ref c) = self.capture {
    // Will emit a video frame in phase 3
    // For now, need access to the surface pixels.
    // This will be plumbed via the app in phase 3.
}
```

For now this is a comment placeholder — the actual frame
capture requires access to the surface pixel buffer which
lives in the app, not the display channel. Phase 3 will
add a `CaptureSession::frame()` call from the app side
when it processes the `DisplayMark` event.

### Step 6: Log capture session start/stop

```rust
info!("capture: writing to {}", dir.display());
// ... on close:
info!("capture: session closed");
```

## Files to create/modify

| File | Changes |
|------|---------|
| `src/config.rs` | Add `--capture` flag |
| `src/capture.rs` | New file: `CaptureSession` |
| `src/main.rs` | Create session, pass to run functions |
| `src/app.rs` | Accept and store capture session, pass to `run_connection` |
| `src/channels/mod.rs` | (no change — events unchanged) |
| `src/channels/main_channel.rs` | Add capture field, call stubs |
| `src/channels/display.rs` | Add capture field, call stubs |
| `src/channels/cursor.rs` | Add capture field, call stubs |
| `src/channels/inputs.rs` | Add capture field, call stubs |

## Success criteria

- `--capture /tmp/test` creates the directory and logs
  "capture: writing to /tmp/test".
- Without `--capture`, behaviour is unchanged.
- All channels compile with the new capture field.
- `pre-commit run --all-files` passes.
- Unit tests still pass.
