# Phase 2: Move pcap writes to a dedicated writer task

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
referenced source files, understand existing patterns (the
`CaptureSink` trait, `CaptureSession`, per-channel
`PcapChannelWriter`, the existing `Arc<[u8]>` payload pattern
in `TrafficBuffers`), and ground your answers in what the code
actually does today. Do not speculate about the codebase when
you could read it instead.

## Goal

Make `CaptureSink::packet_sent` and
`CaptureSink::packet_received` non-blocking enqueues so that
slow disk while `--capture` is active no longer back-pressures
the SPICE socket. All five `PcapChannelWriter` instances move
behind a single dedicated tokio task that drains a bounded
mpsc; frame construction (Ethernet/IPv4/TCP headers, sequence
numbers, segmentation) moves into that task and off the channel
hot path.

Drop-on-overflow semantics are committed by the master plan.
Every channel snapshot gains a `writer_dropped_count` so a bug
report shows whether disk fell behind during the captured
session.

Out of scope for this phase:
- Video / MP4 encoding (`VideoWriter`, `CaptureSession::frame`)
  stays inline on the egui event handler. Phase 3 deals with
  that.
- Tuning the queue capacity beyond the initial choice of 1024.
- Any change to the SPICE protocol or message-parsing layer.

## Design

### Architecture overview

Today (single-threaded write):

```
DisplayChannel::run_loop
  └─ packet_received("display", &chunk[..n])
       └─ CaptureSession::packet_received
            └─ Mutex<PcapChannelWriter>::lock()
                 └─ File::write_all   ← blocks read loop on slow disk
```

After phase 2:

```
DisplayChannel::run_loop
  └─ packet_received("display", &chunk[..n])  → bool
       └─ CaptureSession::packet_received
            └─ mpsc::Sender::try_send (non-blocking)
                 ├─ Ok      → returns true
                 └─ Full    → returns false (caller increments
                              writer_dropped_count for this channel)

[separate writer task]
  loop {
      match rx.recv().await {
          Some(item) => writers[item.channel].write_*(item)
          None       => break   // sender dropped → drain done
      }
  }
```

The trait return-type change is local: only the channel
handlers consume the new `bool` and increment their own
counter on `false`. Frame construction, sequence-number
management, and segmentation all migrate into the writer task,
so the hot path does only an `Arc<[u8]>` allocation and a
`try_send`.

### Queue item

A new private type in `ryll/src/capture.rs`:

```rust
#[derive(Debug)]
enum PcapDirection {
    Sent,
    Received,
}

#[derive(Debug)]
struct PcapQueueItem {
    /// Channel name as a `&'static str` from the existing
    /// `CHANNELS` constant slice. Anything not in `CHANNELS`
    /// is dropped silently by the writer task (existing
    /// behaviour for `webdav`/`playback`).
    channel: &'static str,
    direction: PcapDirection,
    /// Raw SPICE message bytes (including 6-byte mini-header).
    /// `Arc<[u8]>` so the hot-path enqueue copies into a single
    /// allocation that the writer task consumes by reference.
    payload: Arc<[u8]>,
    /// `start.elapsed()` captured at enqueue time, so pcap
    /// timestamps reflect arrival on the wire (not the writer
    /// task's later dequeue).
    elapsed: Duration,
}
```

Capturing `elapsed` at enqueue time is important: if the writer
task falls behind by hundreds of ms, the pcap timeline would
otherwise compress all delayed packets to look like a burst at
dequeue moment, which would mislead a reader of the report.

### Queue and writer task

Bounded `tokio::sync::mpsc::channel::<PcapQueueItem>(1024)`.
1024 entries is the initial choice; rationale:

- Typical SPICE inputs / cursor / main messages are well under
  1 KB; the queue is dominated by raw display chunks (up to
  256 KB each at `display.rs:607`). Worst case 1024 entries ×
  256 KB ≈ 256 MB is an upper bound that will essentially
  never be reached because disk-bound back-pressure would have
  triggered drops long before then.
- In steady state with a keeping-up writer task, the queue
  sits near zero.
- Round number that's easy to reason about; commit a constant
  `PCAP_QUEUE_CAPACITY` and revisit only if real bug reports
  show pathological drop rates with a healthy disk.

The writer task is spawned inside `CaptureSession::new`. It
owns the five `Mutex<PcapChannelWriter>` instances (moved out
of `CaptureSession`) wrapped in a plain `HashMap` since they
are now single-owner. It loops on `rx.recv().await`; when the
sender drops the loop sees `None` and the task exits.

Task body sketch:

```rust
async fn pcap_writer_task(
    mut rx: mpsc::Receiver<PcapQueueItem>,
    mut writers: HashMap<&'static str, PcapChannelWriter>,
) {
    while let Some(item) = rx.recv().await {
        let Some(writer) = writers.get_mut(item.channel) else {
            continue;          // unknown channel → drop
        };
        match item.direction {
            PcapDirection::Sent => {
                writer.write_sent(&item.payload, item.elapsed);
            }
            PcapDirection::Received => {
                writer.write_received(&item.payload, item.elapsed);
            }
        }
    }
}
```

`PcapChannelWriter`'s existing method signatures
(`write_sent`/`write_received` taking `&[u8]` and `Duration`)
don't need to change. The mutex wrapper around each writer is
gone — single owner now.

Frame construction (Ethernet/IPv4/TCP headers, sequence
numbers, segmentation in `write_segmented`,
`build_tcp_frame`) is already encapsulated inside
`PcapChannelWriter`; it moves along with the writer struct
into the task. No callers of `build_tcp_frame` exist outside
`PcapChannelWriter` today, so no other code shifts.

### `CaptureSink` trait change

In `shakenfist-spice-renderer/src/capture_sink.rs`:

```rust
pub trait CaptureSink: Send + Sync {
    /// Enqueue a sent packet for capture. Returns `true` if
    /// the packet was accepted, `false` if the writer queue
    /// was full and the packet was dropped.
    fn packet_sent(&self, channel: &str, data: &[u8]) -> bool;
    /// Enqueue a received packet for capture. Same `bool`
    /// semantics as `packet_sent`.
    fn packet_received(&self, channel: &str, data: &[u8]) -> bool;
    /// Unchanged in phase 2; phase 3 reworks video encoding.
    fn frame(&self, surface_id: u32, pixels: &[u8], width: u32, height: u32);
}
```

The two existing impls update accordingly:

1. `CaptureSession` (`ryll/src/capture.rs`) builds a
   `PcapQueueItem` and calls `queue_tx.try_send(item)`;
   `Ok(()) → true`, `Err(TrySendError::Full(_)) → false`,
   `Err(TrySendError::Closed(_)) → false` (the writer task
   has exited; treat as drop).
2. The no-op stub at `ryll/src/main.rs:7-32` for builds with
   `capture` feature disabled returns `true` from both
   methods — there's no queue, so "accepted" is the correct
   contract (callers must not record drops in the
   no-capture build).

### Per-channel drop counter

Each channel handler gains a `capture_dropped_count: u64`
field on its struct and a matching field on its snapshot in
`shakenfist-spice-renderer/src/snapshots.rs`:

```rust
pub struct DisplaySnapshot {
    // ... existing fields ...
    /// Number of pcap-capture packets dropped because the
    /// writer-task queue was full. Cumulative since session
    /// start. Zero when `--capture` is not in use.
    pub writer_dropped_count: u64,
}
```

Mirror the field in `InputsSnapshot`, `CursorSnapshot`,
`MainSnapshot`, `UsbredirSnapshot`, `WebdavSnapshot`, and
`PlaybackSnapshot` so every channel-state.json answers "did
the writer drop my channel's traffic during this session?".

Call-site update pattern (repeated across all six channel
read paths and all six send paths):

```rust
// Existing:
if let Some(ref c) = self.capture {
    c.packet_received("display", &chunk[..n]);
}

// After phase 2:
if let Some(ref c) = self.capture
    && !c.packet_received("display", &chunk[..n])
{
    self.capture_dropped_count = self.capture_dropped_count.saturating_add(1);
}
```

Mirror in `update_snapshot` (each channel handler has its own
`update_snapshot`). Initialise to zero in each constructor.

`webdav` and `playback` are not in `CHANNELS` and have no
`PcapChannelWriter`. Their queue items are dropped silently at
the writer-task dispatch (existing behaviour preserved). They
still get `writer_dropped_count` on their snapshots so the
field is uniform across all channels; in practice it stays
zero because their items are accepted by the queue and
silently discarded by the task — distinct from a queue-full
drop.

### Lifecycle and shutdown

`CaptureSession` after phase 2:

```rust
pub struct CaptureSession {
    pub dir: PathBuf,
    pub start: Instant,
    /// Sender side of the writer queue. Held in an
    /// `Option<Mutex<>>` so `close()` can `take()` it and
    /// drop the sender, signalling the writer task to drain
    /// and exit.
    queue_tx: Mutex<Option<mpsc::Sender<PcapQueueItem>>>,
    writer_handle: Mutex<Option<tokio::task::JoinHandle<()>>>,
    video_writer: Mutex<Option<VideoWriter>>,
    video_init_attempted: Mutex<bool>,
    closed: AtomicBool,
}
```

`close()` becomes async:

```rust
pub async fn close(&self) {
    if self.closed.swap(true, Ordering::SeqCst) {
        return;
    }
    // Drop the sender so the writer task's recv() returns None.
    drop(self.queue_tx.lock().unwrap().take());
    // Await drain.
    if let Some(handle) = self.writer_handle.lock().unwrap().take() {
        let _ = handle.await;
    }
    // Existing video close path.
    self.finalize_video();
}
```

Every existing `capture.close()` call site
(`ryll/src/main.rs:383`, `ryll/src/main.rs:726`,
`ryll/src/app.rs:2564`, `ryll/src/app.rs:4107`) must become
`capture.close().await`. All four sites are already inside
async functions, so this is mechanical.

`Drop` impl (`ryll/src/capture.rs:664-667`) cannot await. It
does best-effort: take the sender and drop it (writer task
will drain and exit on its own), but cannot block to confirm
drain. This is acceptable because every documented disconnect
/ reconnect path explicitly calls `close().await`; `Drop`
firing without a prior `close()` is the implicit-shutdown
case (process exit) where best-effort is the most we can do
inside a destructor.

### Pcap timestamp ordering

`PcapChannelWriter::write_*` uses its own `client_seq` /
`server_seq` for TCP-sequence consistency *within a channel*.
Across channels Wireshark opens the per-channel pcap files
independently, so cross-channel ordering does not matter. The
queue is FIFO so within-channel ordering is preserved even
when the writer task falls behind.

If two enqueues from the same channel race in time (which
cannot happen today — each channel handler is a single async
task), the queue's FIFO order resolves them. No additional
serialisation is required.

## Steps

### Step 1: Define `PcapQueueItem` and the writer task

1. Add `PcapDirection` and `PcapQueueItem` private types in
   `ryll/src/capture.rs`.
2. Add `const PCAP_QUEUE_CAPACITY: usize = 1024;`.
3. Implement `async fn pcap_writer_task(rx, writers)` as
   sketched above. Loop on `rx.recv().await`; dispatch by
   channel name; ignore unknown channel names.

### Step 2: Rework `CaptureSession`

1. Remove `pcap_writers: HashMap<String, Mutex<PcapChannelWriter>>`
   from `CaptureSession`. Replace with:
   - `queue_tx: Mutex<Option<mpsc::Sender<PcapQueueItem>>>`
   - `writer_handle: Mutex<Option<JoinHandle<()>>>`
2. In `CaptureSession::new`:
   - Construct each `PcapChannelWriter` as before
     (`ryll/src/capture.rs:526+`), but collect them into
     `HashMap<&'static str, PcapChannelWriter>` (no `Mutex`).
   - `mpsc::channel(PCAP_QUEUE_CAPACITY)`.
   - `tokio::spawn(pcap_writer_task(rx, writers))`.
   - Store the sender and handle in the new `Mutex<Option<_>>`
     slots.
3. Replace `CaptureSession::packet_sent`,
   `CaptureSession::packet_received` (lines 600 and 611) with
   the `try_send` form:
   - Build `PcapQueueItem { channel, direction, payload:
     Arc::from(data), elapsed: self.start.elapsed() }`.
   - Match on `try_send`: `Ok` → `true`, `Err(Full)` →
     `false`, `Err(Closed)` → `false`.
   - Channel name passed in as `&str` must be matched against
     the `CHANNELS` constants to obtain the `&'static str`
     used in the queue item (or we change the trait signature
     to take `&'static str` — see Risks).
4. Refactor `close()` per the Design section: make it `async`,
   drop the sender, await the join handle, then call the
   existing video-finalize path.

### Step 3: Update the `CaptureSink` trait and stub

1. In `shakenfist-spice-renderer/src/capture_sink.rs`, change
   `packet_sent` / `packet_received` return type from `()` to
   `bool`. Update the doc comment.
2. In `ryll/src/main.rs:7-32`, the no-capture stub returns
   `true` from both methods unconditionally. `frame()` stays
   unchanged.

### Step 4: Update call sites in all six channels

For each channel handler — `display.rs`, `main_channel.rs`,
`inputs.rs`, `cursor.rs`, `usbredir.rs`, `webdav.rs` — apply
both edits below.

1. Add `capture_dropped_count: u64` to the channel struct and
   initialise to zero in the constructor.
2. At every existing `c.packet_received(...)` and
   `c.packet_sent(...)` call site (12 total — file:line pairs
   listed in the explore survey), wrap in a `!c.packet_*(...)`
   check and increment `self.capture_dropped_count` on
   `false`.
3. In each channel's `update_snapshot`, mirror
   `capture_dropped_count` into the snapshot's new
   `writer_dropped_count` field.

### Step 5: Snapshot field addition

1. In `shakenfist-spice-renderer/src/snapshots.rs`, add
   `writer_dropped_count: u64` to:
   - `DisplaySnapshot`
   - `InputsSnapshot`
   - `CursorSnapshot`
   - `MainSnapshot`
   - `UsbredirSnapshot`
   - `WebdavSnapshot`
   - `PlaybackSnapshot`
2. The default derive on each gives `0`, so existing
   tests that build a default snapshot continue to pass.

### Step 6: Make `close()` async at the four call sites

Edit each of:
- `ryll/src/main.rs:383` (explicit disconnect path)
- `ryll/src/main.rs:726` (reconnect path)
- `ryll/src/app.rs:2564` (renderer-init failure path)
- `ryll/src/app.rs:4107` (UI close-button path)

…to `capture.close().await`. Verify each site is already
inside an async function; from the explore survey all four
are. If any call site is not async-reachable, the Risks
section's "callers must await" mitigation applies.

The `Drop` impl at `ryll/src/capture.rs:664-667` keeps doing
the no-await best-effort path: drop the sender out of
`queue_tx`, do not await the handle.

### Step 7: Tests

The existing `segment_payload_*` unit tests
(`ryll/src/capture.rs:692-799`) keep passing untouched —
their target is the pure-helper layer below
`PcapChannelWriter`. Add new tests below them:

1. **`writer_task_drains_queue_to_pcap_files`**: spawn the
   writer task with a `tempfile::TempDir`, enqueue a handful
   of items across multiple channels, drop the sender, await
   the task, then read the resulting pcap files with
   `PcapReader` and assert one frame per enqueued item per
   channel.

2. **`writer_task_drops_unknown_channel_silently`**: enqueue
   an item with `channel: "webdav"`, confirm the task does
   not panic and produces no file for `webdav` (since it
   isn't in `CHANNELS`).

3. **`try_send_full_returns_false`**: build a
   `CaptureSession` with `PCAP_QUEUE_CAPACITY = 4` (use a
   test-only constructor or a feature flag the test can
   activate), saturate the queue without yielding, observe
   the `packet_received` calls flip from `true` to `false`
   once full.

4. **`close_awaits_drain`**: enqueue N items, call
   `close().await`, then assert all N landed in the pcap
   file (no items lost to shutdown).

5. **`stub_capture_sink_always_returns_true`**: the no-op
   `CaptureSink` for `#[cfg(not(feature = "capture"))]`
   reports `true` from both packet methods, so channels
   don't spuriously count drops in non-capture builds.

6. **Snapshot serialisation**: extend each existing
   `test_*_snapshot_serialises` test in `ryll/src/bugreport.rs`
   to populate `writer_dropped_count` and assert it survives
   serialisation into channel-state.json.

### Step 8: Manual verification

1. `make build`, `make test`, `make lint`, and
   `pre-commit run --all-files` must all pass.
2. Live capture test: run ryll with `--capture <tmpdir>`
   against a working SPICE server, do typical interactive
   work, trigger F12 bug report. Confirm:
   - The per-channel pcap files
     (`<tmpdir>/main.pcap`, `display.pcap`, etc.) open in
     Wireshark and show SPICE traffic.
   - `channel-state.json` contains `writer_dropped_count: 0`
     for every channel under healthy disk.
3. Disk-stress test (best-effort, see Risks): run capture
   onto a slow medium (e.g. a fuse filesystem with an
   inserted delay, or a `tmpfs` saturated by a separate
   `dd if=/dev/urandom of=/path/big bs=1M`). Confirm
   `writer_dropped_count` rises and that the rendered
   session does **not** stutter — that is the whole point of
   this phase.

### Step 9: Documentation

1. Update the new "Reading display `channel-state.json`…"
   section in `docs/troubleshooting.md` (added in phase 1)
   to mention `writer_dropped_count` and how a non-zero
   value implicates disk rather than decode or socket-read.
2. Update `ARCHITECTURE.md`'s snapshot-table row for each
   channel to mention the new field.
3. Update the master plan's execution table to mark phase 2
   `Done`.

## Administration and logistics

### Success criteria

- `CaptureSink::packet_sent` and
  `CaptureSink::packet_received` return `bool` everywhere
  and are non-blocking (the implementation does a single
  `Arc::from(slice)` allocation plus `try_send`).
- All five `PcapChannelWriter` instances live inside one
  dedicated tokio task; no `Mutex<PcapChannelWriter>`
  remains in the codebase.
- Every channel snapshot exposes `writer_dropped_count`.
- `CaptureSession::close()` is `async`, awaits the writer
  task's drain, and is `.await`'d at all four explicit close
  call sites.
- All existing capture / pcap tests keep passing.
- New tests in step 7 pass.
- A live capture session produces valid per-channel pcap
  files; bug-report `channel-state.json` shows
  `writer_dropped_count: 0` on healthy disk and rises on
  slow disk without the rendered session stuttering.
- `make build`, `make test`, `make lint`, and
  `pre-commit run --all-files` all pass.

### Risks

- **`close()` becoming `async`** is a small API ripple. All
  four explicit call sites are inside async functions and
  the change is mechanical. The `Drop` path remains
  best-effort (no `.await` possible inside a destructor);
  process-exit shutdowns may lose a tail of buffered
  packets, which is identical to the current behaviour on
  abrupt termination.
- **Channel name as `&'static str`** in `PcapQueueItem`. The
  trait method takes `&str`. Internally the
  `CaptureSession::packet_*` impl `match`es the input
  against the `CHANNELS` array and uses the matched
  `&'static str` for the queue item; an unrecognised name
  causes immediate drop with `true` returned (consistent
  with today's silent drop at HashMap lookup). Alternative:
  carry an owned `String` — rejected because every enqueue
  would allocate, defeating the hot-path gain. Alternative:
  carry an enum — viable but adds a coupling between the
  trait surface and the pcap layer; the `&'static str` match
  is the lightest option.
- **`Arc::from(&[u8])` allocation per enqueue.** One
  allocation per SPICE message in the worst case. Each
  allocation is small (tens of ns); compared to the disk
  write it replaces this is well under the noise floor. If
  profiling later shows it as a hot spot, the
  `TrafficBuffers` ring already produces `Arc<[u8]>` for the
  same payloads — phase 2 can be revisited to share that
  allocation. Not in scope for this phase.
- **Queue capacity 1024 is a guess.** Steady-state usage
  should sit near zero; worst-case display burst of 256 KB
  chunks × 1024 = 256 MB is an upper bound that exists only
  on a pathologically slow disk. If real bug reports show
  consistent high drop rates with a healthy disk, the cap
  is the first lever to revisit.
- **No regression test for the actual "doesn't stall the
  read loop" property.** The unit tests cover semantics
  (queue drains, drops counted, close awaits) but not the
  performance claim. The performance claim is validated by
  the step-8 disk-stress manual check. A future automated
  test could measure read-loop iteration latency under
  artificial disk delay; out of scope here.
- **Order across channels in the pcap timeline.** Each
  channel has its own pcap file, so cross-channel order is
  not visible in Wireshark. The within-channel order is
  preserved by the FIFO queue. If a future use case needs a
  single unified pcap with strict cross-channel ordering,
  that would require a single multiplexed writer — not in
  scope for phase 2.
- **`webdav` and `playback` writer-dropped accounting.**
  Both channels call `packet_*` but have no
  `PcapChannelWriter`. With the new design their items are
  accepted by the queue and silently dropped by the writer
  task on dispatch. Their `writer_dropped_count` therefore
  stays zero in normal operation and only rises if the
  queue itself is full. This is the correct semantics — the
  counter measures queue pressure, not channel-configured
  status.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
