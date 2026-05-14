# Phase 3: Move MP4 video encoding off the GUI event loop

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
referenced source files, understand existing patterns (the
`CaptureSink` trait, the phase-2 `pcap_writer_task`,
`VideoWriter`'s lazy initialisation, the egui `DisplayMark`
event flow), and ground your answers in what the code actually
does today. Do not speculate about the codebase when you could
read it instead.

## Goal

Move `VideoWriter::write_frame` and `VideoWriter::close` (the
MP4 moov-atom finalisation) off the egui event loop onto a
dedicated tokio encoder task. After phase 3 the egui side of
`CaptureSession::frame()` is a non-blocking enqueue that
returns `bool`; H.264 encoding and MP4 muxing run on the
tokio runtime alongside the pcap writer task from phase 2.

This phase removes the last inline disk/CPU work on the
display path. The egui frame loop's per-frame overhead under
`--capture` drops from a 10–30 ms H.264 encode + MP4 mux to
an `Arc::from(pixels)` allocation plus an mpsc `try_send`.

Out of scope for this phase:
- Any change to the H.264 encoder configuration or to the
  MP4 container layout.
- Frame-pool memory optimisation to avoid the per-enqueue
  `Arc::from(pixels)` copy. Listed under Risks for follow-up
  if real bug reports show it matters.
- Pcap writer task (phase 2 territory).
- Renderer-side `ImageReady` arrival-to-display latency
  (phase 4 territory).

## Design

### Architecture overview

Today (after phase 2 lands, before phase 3):

```
egui App::update
  └─ process_events
       └─ ChannelEvent::DisplayMark
            └─ CaptureSession::frame(surface_id, pixels, w, h)
                 └─ Mutex<Option<VideoWriter>>::lock()
                      ├─ first frame? VideoWriter::new()  ← blocks
                      └─ write_frame() ← blocks (encode + mux)
```

After phase 3:

```
egui App::update
  └─ process_events
       └─ ChannelEvent::DisplayMark
            └─ CaptureSession::frame(surface_id, pixels, w, h)  → bool
                 └─ mpsc::Sender::try_send(VideoQueueItem)
                      ├─ Ok    → return true
                      └─ Full  → return false (caller bumps
                                 self.video_drop_count)

[separate encoder task]
  loop {
      match rx.recv().await {
          Some(item) => {
              if video_writer.is_none() && !init_attempted {
                  video_writer = VideoWriter::new(...)
                  init_attempted = true
              }
              if let Some(vw) = video_writer.as_mut() {
                  vw.write_frame(&item.pixels, item.width,
                                  item.height, item.ts_ms);
              }
          }
          None => break;   // sender dropped → drain done
      }
  }
  // After drain, finalise MP4 (writes moov atom) and exit.
  if let Some(mut vw) = video_writer.take() {
      vw.close();
  }
```

The lazy-init pattern (`VideoWriter::new` runs on the first
frame so the encoder gets real dimensions from the surface)
stays — it just runs on the encoder task instead of the egui
thread. `init_attempted` and `video_writer` are task-local
state instead of fields on `CaptureSession`.

### Queue item

A new private type in `ryll/src/capture.rs`:

```rust
#[derive(Debug)]
struct VideoQueueItem {
    surface_id: u32,
    /// Raw RGBA pixel data for the surface as `Arc<[u8]>`.
    /// Typical sizes: ~5 MB at 1440×900, ~8 MB at 1920×1080,
    /// ~33 MB at 3840×2160. The enqueue copy is the price we
    /// pay to keep the egui thread non-blocking; see Risks.
    pixels: Arc<[u8]>,
    width: u32,
    height: u32,
    /// `start.elapsed().as_millis() as u64`, captured at
    /// enqueue time so MP4 presentation timestamps reflect
    /// when the frame was produced, not when the encoder
    /// task got around to it.
    timestamp_ms: u64,
}
```

`surface_id` is carried even though today only surface 0 is
recorded (`capture.rs:623-625`) — keeping it lets the encoder
task apply the policy check, matching today's behaviour.

### Queue and encoder task

Bounded `tokio::sync::mpsc::channel::<VideoQueueItem>(8)`.
Cap rationale:

- One `DisplayMark` per server frame; typical SPICE
  presentation rates are tens of fps. The encoder takes
  10–30 ms per 1080p frame, so an 8-slot queue absorbs
  ~100–250 ms of encoder backlog before drops begin.
- Worst-case memory: 8 × ~33 MB at 4K = ~264 MB. Steady
  state with a keeping-up encoder is near zero. The cap is
  tunable; commit as `VIDEO_QUEUE_CAPACITY = 8` and revisit
  only if real captures show it isn't enough.
- Smaller than `PCAP_QUEUE_CAPACITY = 1024` because video
  payloads are 5–8 MB each vs. pcap's typical sub-1-KB
  messages. Bound is dominated by per-item size, not item
  count.

The encoder task body (sketch):

```rust
async fn video_writer_task(
    mut rx: mpsc::Receiver<VideoQueueItem>,
    dir: PathBuf,
) {
    let mut writer: Option<VideoWriter> = None;
    let mut init_attempted = false;

    while let Some(item) = rx.recv().await {
        if item.surface_id != 0 {
            // Today only surface 0 is recorded; matches the
            // existing CaptureSession::frame() policy check.
            continue;
        }
        if writer.is_none() && !init_attempted {
            init_attempted = true;
            writer = VideoWriter::new(&dir, &item.pixels,
                                       item.width, item.height,
                                       item.timestamp_ms);
            // VideoWriter::new() writes the first frame as
            // part of init, so no separate write_frame call
            // for the init frame.
            continue;
        }
        if let Some(vw) = writer.as_mut() {
            vw.write_frame(&item.pixels, item.width,
                            item.height, item.timestamp_ms);
        }
    }
    // Sender dropped → write the MP4 moov atom and exit.
    if let Some(mut vw) = writer.take() {
        vw.close();
    }
    debug!("capture: video writer task drained and exiting");
}
```

`VideoWriter` is `Send` (`openh264-sys2` is FFI-opaque data;
the encoder doesn't keep thread-affine state). Moving it into
the task by value is straightforward.

### `CaptureSink::frame` trait change

In `shakenfist-spice-renderer/src/capture_sink.rs`:

```rust
pub trait CaptureSink: Send + Sync {
    fn packet_sent(&self, channel: &str, data: &[u8]) -> bool;
    fn packet_received(&self, channel: &str, data: &[u8]) -> bool;
    /// Record a display frame after a MARK boundary. Returns
    /// `true` if the frame was queued, `false` if the encoder
    /// task's queue was full and the frame was dropped. The
    /// caller (the egui frame loop) bumps a drop counter on
    /// `false`.
    fn frame(&self, surface_id: u32, pixels: &[u8],
             width: u32, height: u32) -> bool;
}
```

Both impls update accordingly:

1. `CaptureSession` (`ryll/src/capture.rs`) builds a
   `VideoQueueItem` (allocating `Arc::from(pixels)` once) and
   calls `video_tx.try_send(item)`. `Ok(()) → true`,
   `Err(Full) → false`, `Err(Closed) → false` (post-close
   path; treat as drop).
2. The no-op stub at `ryll/src/main.rs` returns `true`
   unconditionally — there's no queue to fill in the
   non-capture build.

### `CaptureSession` after phase 3

```rust
pub struct CaptureSession {
    pub dir: PathBuf,
    pub start: Instant,
    // Phase 2 (pcap):
    queue_tx: Mutex<Option<mpsc::Sender<PcapQueueItem>>>,
    writer_handle: Mutex<Option<JoinHandle<()>>>,
    // Phase 3 (video):
    video_tx: Mutex<Option<mpsc::Sender<VideoQueueItem>>>,
    video_handle: Mutex<Option<JoinHandle<()>>>,
    closed: AtomicBool,
}
```

The old `video_writer: Mutex<Option<VideoWriter>>` and
`video_init_attempted: Mutex<bool>` fields are **deleted**.
Their state is now task-local inside `video_writer_task`.

`CaptureSession::new` spawns the encoder task alongside the
pcap writer task:

```rust
let (video_tx, video_rx) = mpsc::channel(VIDEO_QUEUE_CAPACITY);
let video_handle = tokio::spawn(video_writer_task(video_rx, dir.clone()));
```

`CaptureSession::close` (kept sync per phase 2's decision)
drops both senders and detaches both handles:

```rust
pub fn close(&self) {
    if self.closed.swap(true, Ordering::SeqCst) { return; }
    drop(self.queue_tx.lock().unwrap().take());
    let _ = self.writer_handle.lock().unwrap().take();
    drop(self.video_tx.lock().unwrap().take());
    let _ = self.video_handle.lock().unwrap().take();
    info!("capture: session closed ({})", self.dir.display());
}
```

The encoder task runs on the tokio runtime until both
`recv()` returns `None` and the post-loop `vw.close()` (moov
write) completes. The video file becomes playable only after
the task finalises.

### MP4 finalisation: explicit regression and mitigation

Today (post-phase-2) `CaptureSession::close()` calls
`vw.close()` synchronously inside its own body, so by the
time `close()` returns the MP4 has its moov atom and is
playable. After phase 3 that work moves to the encoder task,
which runs *on the tokio runtime*. `close()` returns
immediately after dropping the sender; the encoder task may
not have finalised by then. In two scenarios this matters:

1. **Bug report submitted seconds after disconnect** (the
   user clicks Close on the disconnect dialog, then F12 →
   submit). `BugReport::assemble` reads the MP4 file off
   disk; if the encoder task hasn't run `write_end()` yet,
   the file lacks the moov atom and is unplayable.
2. **SIGINT-style abrupt shutdown** (`app.rs:2564`). The
   tokio runtime is torn down quickly after the egui
   viewport closes; the encoder task may not get a chance to
   complete its final `write_end()`.

For scenario 1, the encoder task drains quickly (a few
frames at most in the queue), so in practice the gap is
small — but it is not zero, and we should expect occasional
unplayable bug-report MP4s.

For scenario 2, the runtime usually keeps polling until the
top-level `tokio::main` future returns. In `run_headless()`
and `run_gui()` the future returns from
`spawn_orchestrator(...).await`, after which `close()` runs.
If those async functions return on the same tokio thread
that owns the runtime, the encoder task gets cycles before
the runtime drops. But there is no hard guarantee.

**Phase 3 accepts this regression.** Mitigation is a
follow-up if real bug reports show unplayable MP4s:

- **Bounded synchronous wait in `close()`.** After dropping
  the sender, take the `JoinHandle` and do
  `tokio::runtime::Handle::current().block_on(
      tokio::time::timeout(Duration::from_millis(500), handle)
  )`. From egui's sync `App::update` this is feasible because
  the egui thread is not a tokio worker (it doesn't conflict
  with `block_on`). 500 ms is short enough not to feel like a
  hang, long enough to absorb realistic drain time.
- **Async `flush()` method** called from the async close
  sites (`main.rs:391`, `main.rs:733`) while the sync paths
  stay best-effort.

Either is a small follow-up patch; neither is needed to land
phase 3.

### Drop-counter placement: `AppSnapshot`

Pcap's `writer_dropped_count` lives on every channel snapshot
because pcap drops are caused per-channel by the per-channel
hot path. Video frames come from the egui frame loop, not
from a SPICE channel handler. The natural home for the video
drop counter is therefore `AppSnapshot`:

```rust
// In ryll/src/bugreport.rs::AppSnapshot
pub video_drop_count: u64,
```

Maintained as a `u64` field on `RyllApp`:

```rust
// In ryll/src/app.rs
video_drop_count: u64,
```

Bumped at the existing `frame()` call site in
`process_events`:

```rust
// Today (post phase-2):
if let Some(ref c) = self.capture {
    c.frame(0, surface.pixels(), surface.width, surface.height);
}

// After phase 3:
if let Some(ref c) = self.capture {
    if !c.frame(0, surface.pixels(), surface.width, surface.height) {
        self.video_drop_count = self.video_drop_count.saturating_add(1);
    }
}
```

Mirrored into `AppSnapshot.video_drop_count` from
`update_app_snapshot()` at `ryll/src/app.rs:1917-1956`.

## Steps

### Step 1: Define `VideoQueueItem` and the encoder task

1. Add the private `VideoQueueItem` struct and
   `const VIDEO_QUEUE_CAPACITY: usize = 8;` in
   `ryll/src/capture.rs` (near the phase-2
   `PcapQueueItem` / `PCAP_QUEUE_CAPACITY` definitions).
2. Implement `async fn video_writer_task(rx, dir)` as
   sketched in the Design section. Lazy-init on first
   frame (matching today's `CaptureSession::frame` first-call
   behaviour); on sender drop, finalise via `vw.close()` and
   exit.

### Step 2: Move `VideoWriter` ownership into the task

1. Delete the `video_writer: Mutex<Option<VideoWriter>>` and
   `video_init_attempted: Mutex<bool>` fields from
   `CaptureSession`.
2. Add `video_tx: Mutex<Option<mpsc::Sender<VideoQueueItem>>>`
   and `video_handle: Mutex<Option<JoinHandle<()>>>` fields.
3. In `CaptureSession::new`, spawn the encoder task with
   `mpsc::channel(VIDEO_QUEUE_CAPACITY)`; store sender and
   handle.

### Step 3: Rewrite `CaptureSession::frame()` as a non-blocking enqueue

Replace the current synchronous implementation at
`ryll/src/capture.rs:622-642` with:

```rust
pub fn frame(&self, surface_id: u32, pixels: &[u8],
             width: u32, height: u32) -> bool {
    let tx_guard = self.video_tx.lock().unwrap();
    let Some(tx) = tx_guard.as_ref() else {
        return false; // closed
    };
    let item = VideoQueueItem {
        surface_id,
        pixels: Arc::from(pixels),
        width,
        height,
        timestamp_ms: self.start.elapsed().as_millis() as u64,
    };
    tx.try_send(item).is_ok()
}
```

The surface-0 filter moves into the task (the encoder skips
non-zero `surface_id`s on dequeue). Filtering in `frame()`
instead would still allocate the `Arc<[u8]>` only to discard
it; checking in the task keeps the hot path uniformly cheap.

### Step 4: Update `CaptureSession::close()` and `Drop`

1. Extend `close()` to also drop `video_tx` and detach
   `video_handle`, mirroring the pcap path.
2. Remove the inline `video_writer.lock()` + `vw.close()`
   block — that work now happens inside the encoder task.
3. `Drop` already delegates to `close()`; no separate
   change needed.

### Step 5: Update the `CaptureSink::frame` trait

1. In `shakenfist-spice-renderer/src/capture_sink.rs` change
   `fn frame(...)` from `-> ()` to `-> bool`. Update the doc
   comment to describe the queue-full semantics.
2. Update `CaptureSession`'s trait impl
   (`ryll/src/capture.rs`) to return the new `bool` from
   `CaptureSession::frame`.
3. Update the no-op stub at `ryll/src/main.rs` to return
   `true` from `frame()` unconditionally.

### Step 6: Track and surface `video_drop_count`

1. Add `video_drop_count: u64` to `AppSnapshot` in
   `ryll/src/bugreport.rs`.
2. Add `video_drop_count: u64` to `RyllApp` in
   `ryll/src/app.rs`; initialise to zero.
3. At the existing `frame()` call site (around
   `ryll/src/app.rs:1615`), wrap in a `!c.frame(...)` check
   and bump `self.video_drop_count` on `false`.
4. In `update_app_snapshot()`
   (`ryll/src/app.rs:1917-1956`), set
   `snap.video_drop_count = self.video_drop_count;`.

### Step 7: Tests

1. **`video_writer_task_encodes_and_finalises_mp4`**:
   spawn the encoder task with a `tempfile::TempDir`,
   enqueue a small number of frames (e.g. 3 frames at
   64×64 to keep encoder time bounded), drop the sender,
   await the handle, then read the produced MP4 with the
   `mp4` crate's `Mp4Reader` and assert it has a valid
   `moov` track and the expected number of samples.
2. **`video_writer_task_skips_non_primary_surfaces`**:
   send one frame with `surface_id: 1` followed by frames
   with `surface_id: 0`; assert the non-zero surface frame
   is not in the output.
3. **`capture_session_frame_returns_false_when_video_queue_full`**:
   on a `current_thread` tokio runtime, hammer
   `session.frame(...)` without yielding; assert
   `accepted > 0` and `dropped > 0`. (Same pattern as the
   phase-2 pcap saturation test.)
4. **`capture_session_close_idempotent_video`**: call
   `close()` twice; the second call is a no-op. After
   close, `frame()` returns `false`.
5. **Snapshot serialisation**: extend
   `test_app_snapshot_serialises` in `ryll/src/bugreport.rs`
   to populate `video_drop_count` and assert it appears in
   the JSON.

### Step 8: Build, test, lint, pre-commit gates

`make build`, `make test`, `make lint`, and
`pre-commit run --all-files` all pass.

### Step 9: Documentation

1. Update the "Reading display `channel-state.json` for
   'video not keeping up'" section in
   `docs/troubleshooting.md` to mention the new
   `session.json`-side `video_drop_count` field (note:
   `AppSnapshot` is serialised to `session.json`, not
   `channel-state.json` — verify the destination filename
   while editing).
2. Update `ARCHITECTURE.md`'s `AppSnapshot` row to mention
   the new field; also note the encoder-task pattern next
   to the pcap-task description.
3. Mark phase 3 `Done` in the master plan's execution
   table.
4. **Optional**: add a short note to
   `docs/troubleshooting.md` about the MP4-finalisation
   regression and the bounded `block_on` mitigation as a
   future follow-up.

## Administration and logistics

### Success criteria

- `CaptureSink::frame` returns `bool` everywhere and is
  non-blocking (one `Arc::from(slice)` allocation +
  `try_send`).
- `VideoWriter` is owned exclusively by the encoder task;
  no `Mutex<Option<VideoWriter>>` remains in the codebase.
- `AppSnapshot` carries `video_drop_count`, surfaced via
  `session.json` in bug reports.
- A live capture session produces a playable MP4 (encoder
  task finalises before process exit on normal shutdown).
- All existing capture / pcap / segment_payload tests pass.
- New tests in step 7 pass.
- `make build`, `make test`, `make lint`, and
  `pre-commit run --all-files` all pass.

### Risks

- **MP4 finalisation is no longer synchronous with
  `close()`.** Documented above in the Design section.
  Accepted regression; mitigation paths exist if it bites.
  This is the most consequential change in phase 3 and
  deserves explicit call-out in the commit message and the
  troubleshooting docs.
- **`Arc::from(pixels)` allocates a fresh ~5–8 MB buffer per
  frame at typical resolutions.** Capture mode is dev-only;
  the allocator pressure is acceptable. If profiling shows
  it as a hot spot under sustained capture, a small frame
  pool (a `Mutex<Vec<Arc<[u8]>>>` of recyclable buffers)
  would amortise the cost without changing the public API.
  Out of scope for phase 3.
- **Worst-case queue memory at 4K is ~264 MB.** Documented;
  the constant `VIDEO_QUEUE_CAPACITY` is the lever. A user
  capturing 4K with a slow disk could see ryll's RSS climb
  by this much before drops begin. Likely acceptable for
  diagnostic mode but worth knowing.
- **MP4 reader test depends on the `mp4` crate's reader.**
  The crate is already a `capture`-feature dependency
  (`ryll/Cargo.toml`) and is what `VideoWriter` itself uses
  to write the file; using its reader to read it back is
  not a new dependency. Confirm during step 7.
- **Encoder errors are silently logged.**
  `VideoWriter::write_frame` already swallows encode/write
  errors with `warn!()` (`ryll/src/capture.rs:492-495`,
  `533-536`). Phase 3 inherits this behaviour. A future
  improvement could surface persistent encoder failures via
  a counter on `AppSnapshot` (e.g. `video_encode_errors`),
  but that's not gated by phase 3.
- **The encoder task only finalises when the sender is
  dropped.** If the runtime is torn down before the task's
  post-loop `vw.close()` runs, the MP4 file is unfinalised.
  Same risk class as the existing pcap-drain best-effort.
- **No automated test for the "doesn't stall the egui loop"
  property.** Step-7's tests cover semantics (encoder task
  drains, finalises, drops counted) but not the
  egui-thread-latency claim. The phase 1 instrumentation
  already exposes the data needed to validate the claim
  empirically from a real bug report. Not in scope to add a
  microbenchmark for this in phase 3.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
