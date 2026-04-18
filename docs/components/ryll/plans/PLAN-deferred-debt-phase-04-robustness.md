# Phase 4: Robustness and safety

Part of [PLAN-deferred-debt.md](/components/ryll/plans/PLAN-deferred-debt/).

## Scope

Address missing error handling, unsafe platform
assumptions, and ungraceful shutdown paths. Four
sub-tasks:

| Step | Description | Source |
|------|-------------|--------|
| 4a | Abrupt exit in disconnect dialog | PLAN-pr20-followup.md #6 |
| 4b | Audio playback shutdown handling | PLAN-pr23-followup.md #7 |
| 4cd | Dedicated audio thread (replaces 4c + 4d) | PLAN-pr23-followup.md #6, #10 |
| 4e | Silent drop logging in build_tcp_frame | PLAN-pr20-followup.md #8 |

## 4a. Abrupt exit in disconnect dialog

### Bug

The disconnect dialog's "Close" button at app.rs:1969
calls `std::process::exit(0)`, which bypasses all
destructors and Drop impls. This can:

- Corrupt capture MP4 files (the moov atom is written
  in the `Drop` impl of `Mp4Writer`).
- Lose unflushed bug report data.
- Leave the audio device in an inconsistent state.

### Fix

Replace `std::process::exit(0)` with the same graceful
shutdown pattern used by the Ctrl+C handler at
app.rs:882-888:

```rust
if ui.button("Close").clicked() {
    if let Some(ref capture) = self.capture {
        capture.close();
    }
    ctx.send_viewport_cmd(egui::ViewportCommand::Close);
}
```

This allows eframe to run its normal shutdown path,
executing all Drop impls. The `ctx` variable is already
in scope (it's the `&egui::Context` parameter of the
`update()` method).

### Files to modify

- `ryll/src/app.rs` -- disconnect dialog "Close" button

### Complexity

Trivial -- a 3-line replacement.

## 4b. Audio playback shutdown handling

### Bug

The playback channel's `run()` loop (playback.rs:215-243)
is a bare `loop { read().await }` with no shutdown check.
During graceful shutdown, it blocks on the socket read
until the tokio runtime is dropped, preventing clean
release of the cpal audio stream.

### Context

**No other channel checks SHUTDOWN_REQUESTED either.**
All channels (display, cursor, inputs) use the same bare
loop pattern and rely on tokio runtime cancellation.
However, the playback channel is the one that
specifically benefits from clean shutdown because it
holds the `cpal::Stream` which should be dropped
properly to release the audio device.

`SHUTDOWN_REQUESTED` is an `AtomicBool` defined in
`main.rs:43`, set by the Ctrl+C handler, and checked by
the GUI loop at app.rs:882 and the headless loop at
app.rs:2390.

### Fix

Wrap the socket read in `tokio::select!` with a
periodic shutdown check, following the headless loop
pattern:

```rust
pub async fn run(&mut self) -> Result<()> {
    info!("playback: channel started");
    loop {
        let mut chunk = [0u8; 65536];
        let stream = &mut self.stream;
        tokio::select! {
            result = async {
                match stream {
                    SpiceStream::Plain(s) => {
                        use tokio::io::AsyncReadExt;
                        s.read(&mut chunk).await
                    }
                    SpiceStream::Tls(s) => {
                        use tokio::io::AsyncReadExt;
                        s.read(&mut chunk).await
                    }
                }
            } => {
                let n = result?;
                if n == 0 {
                    // ... existing disconnect handling
                    break;
                }
                // ... existing message processing
            }
            _ = tokio::time::sleep(
                Duration::from_millis(100)
            ) => {
                if crate::SHUTDOWN_REQUESTED
                    .load(Ordering::Relaxed)
                {
                    info!("playback: shutdown requested");
                    break;
                }
            }
        }
    }
    Ok(())
}
```

The `self.stream` must be extracted into a local
reference before the `select!` to avoid borrow conflicts
with `self` across the async branches (the same pattern
used in main_channel.rs:115).

### Files to modify

- `ryll/src/channels/playback.rs` -- `run()` method

### Complexity

Low-medium. The `tokio::select!` transformation is
straightforward but requires care with mutable borrows.

## 4cd. Dedicated audio thread

This item replaces the originally separate items 4c
(Audio Linux-only gate) and 4d (Mutex lock in audio
callback) with a single architectural change that
solves both problems.

### Bugs addressed

**4c -- Platform safety:** `unsafe impl Send for
SendStream` (playback.rs:75) is only sound on Linux
where ALSA is thread-safe. On macOS (CoreAudio) and
Windows (WASAPI), the cpal stream has thread-affinity
requirements that make this unsound. Ryll's packaging
targets include macOS and Windows.

**4d -- Mutex contention in real-time callback:** The
cpal audio callback locks two mutexes:
`Arc<Mutex<VecDeque<i16>>>` (audio buffer, shared with
the network thread) and `Arc<Mutex<Resampler>>` (only
used by the callback). If the network thread holds the
buffer lock during a large push, the audio callback
blocks and glitches.

### Root cause

Both bugs stem from the same architectural decision:
storing the cpal stream inside `PlaybackChannel`, which
lives in a tokio task. This forces the stream to be
`Send` (for `tokio::spawn`) and forces inter-thread
communication to go through mutexes.

### Fix: dedicated audio thread

Spawn a dedicated `std::thread` for audio output. The
cpal stream is created and lives entirely on that
thread, so it never needs to be `Send`. Communication
between the tokio task and the audio thread uses a
lock-free ring buffer.

```
tokio task (network)          audio thread
┌──────────────────┐         ┌──────────────────┐
│ SPICE DATA msg   │         │ cpal callback    │
│ → Opus/PCM decode│──ring──▶│ → Resampler      │
│ → push samples   │ buffer  │ → volume         │
│                  │         │ → output device   │
└──────────────────┘         └──────────────────┘
```

This is the same architecture that spice-gtk uses
(dedicated GStreamer pipeline thread) and what the
existing `PLAN-pr23-followup.md` comment on the unsafe
impl hints at.

### Detailed changes

**1. Add `rtrb` dependency.**

Add `rtrb = "0.3"` to `ryll/Cargo.toml`. The `rtrb`
crate provides a single-producer single-consumer
lock-free ring buffer designed for real-time audio.
Alternative: `ringbuf = "0.4"`.

**2. Create the audio thread in `start_audio_output`.**

Instead of building the cpal stream in the tokio task
and storing it in `PlaybackChannel._cpal_stream`:

- Create an `rtrb::RingBuffer<i16>` and split it into
  `(producer, consumer)`.
- Store the `producer` in `PlaybackChannel` (replacing
  `audio_buffer: AudioBuffer`).
- Spawn a `std::thread::Builder::new().name("audio")`
  thread that:
  - Receives the `consumer`, `VolumeControl` clone,
    source `sample_rate`, source `channels`, and a
    shutdown flag (`Arc<AtomicBool>`).
  - Opens the cpal device and builds the output stream
    using the source channel count (as per phase 2d).
  - The `Resampler` is created and owned on this thread
    (no mutex needed).
  - The cpal callback reads from the consumer into a
    small local `VecDeque`, then calls
    `resampler.next_frame()` as before.
  - The thread runs until the shutdown flag is set or
    the consumer is exhausted and the flag is set.
- Store the `JoinHandle` and shutdown flag in
  `PlaybackChannel` so `Drop` can signal shutdown and
  join the thread.

**3. Remove `SendStream` and `unsafe impl Send`.**

The cpal stream now lives on the audio thread and never
crosses thread boundaries. `SendStream` and its `unsafe
impl Send` are deleted entirely.

**4. Update the producer side.**

In `process_messages` DATA handler (currently at
playback.rs:339-373), replace
`self.audio_buffer.lock().unwrap().push_back(s)` with
`self.audio_producer.push(s)`. If the ring buffer is
full, samples are dropped (the existing
`MAX_AUDIO_BUFFER_SAMPLES` cap has the same effect).

**5. Update `write_samples_i16` / `write_samples_f32`.**

These functions currently take `&AudioBuffer` and lock
the mutex. Replace with a function that:
- Drains available samples from `rtrb::Consumer` into a
  thread-local `VecDeque`.
- Calls `resampler.next_frame()` on the local
  `VecDeque` as before.
- The resampler's `next_frame` API (which uses
  `VecDeque` random access) is unchanged.

The thread-local `VecDeque` acts as a small lookahead
buffer. It is topped up at the start of each callback
invocation and consumed by the resampler. This keeps
the ring buffer fast path lock-free while preserving
the resampler's interpolation logic.

**6. Shutdown.**

When `PlaybackChannel` is dropped (or `STOP` is
received):
- Set the shutdown `AtomicBool`.
- Drop the producer (this signals end-of-stream to the
  consumer).
- Join the audio thread.
- The thread drops the cpal stream and the audio device
  is released cleanly.

### Files to modify

- `ryll/Cargo.toml` -- add `rtrb`
- `ryll/src/channels/playback.rs` -- major refactor:
  remove `SendStream` / `unsafe impl Send`, replace
  `AudioBuffer` with `rtrb::Producer`, add audio thread
  spawn, update `write_samples_*`, update `Drop`
- No changes needed in `app.rs` -- the
  `PlaybackChannel` public API is unchanged

### Testing

- `cargo test --workspace` must pass.
- Manual test: play audio through a VM session, verify
  clean playback without glitches.
- The `unsafe impl Send` removal is the key
  correctness win -- if it compiles without it, the
  platform safety issue is resolved.

### Complexity

Medium. The audio thread spawn and ring buffer plumbing
is straightforward. The main subtlety is the
callback-local `VecDeque` bridge between the ring buffer
consumer and the resampler's random-access API. The
public API of `PlaybackChannel` does not change, so
`app.rs` and other callers are unaffected.

### Risk

Low-medium. The architectural change is well-understood
(dedicated audio thread is the standard pattern for
real-time audio applications). The ring buffer
introduces a fixed capacity that could cause sample
drops under extreme load, but the existing
`MAX_AUDIO_BUFFER_SAMPLES` cap already does this. The
main risk is subtle bugs in the shutdown/join sequence
or in the `VecDeque` bridge logic.

## 4e. Silent drop logging in build_tcp_frame

### Bug

`build_tcp_frame` (capture.rs:129-130) returns an empty
`Vec` when `ip_payload_len > 65515` with no logging.
This silently drops oversized packets from capture files.

The `write_segmented()` method already chunks payloads
into 65495-byte segments, so this guard should rarely
trigger from internal callers. However,
`build_tcp_frame` is `pub(crate)` and also called from
`bugreport.rs`, where the caller may not chunk.

### Fix

Add a `warn!` before the return:

```rust
if ip_payload_len > 65515 {
    warn!(
        "build_tcp_frame: payload too large for IPv4 \
         ({} bytes), dropping",
        ip_payload_len
    );
    return Vec::new();
}
```

**Note:** The `decode_mjpeg_frame` silent return (the
other half of the original PLAN-pr20-followup.md items
#8 and #9) was already fixed in phase 1 step 1d.

### Files to modify

- `ryll/src/capture.rs` -- `build_tcp_frame`

### Complexity

Trivial -- adding one `warn!` line.

## Commit sequence

1. **4a**: Replace `std::process::exit(0)` with graceful
   shutdown (standalone, trivial).
2. **4e**: Add warn log to `build_tcp_frame`
   (standalone, trivial).
3. **4b**: Add shutdown handling to playback's `run()`
   (standalone, low-medium).
4. **4cd**: Dedicated audio thread with lock-free ring
   buffer (medium, depends on 4b being committed first
   since both modify the playback channel).

Steps 4a and 4e are independent. Step 4b should land
before 4cd since both modify `playback.rs` and 4cd
will build on the shutdown infrastructure added in 4b.

## Administration

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
