# Diagnostics and instrumentation

Ryll carries a set of facilities for understanding what a session did:
statistics collection, an in-memory traffic ring buffer, channel state
snapshots, bug report assembly, a live traffic viewer, and a capture mode
that records per-channel protocol pcaps and H.264 video of the display to
a directory.

## Statistics and Instrumentation

Ryll tracks:

- **FPS**: Sliding-window frames-per-second derived from `DisplayMark`
  boundaries (true frame completions), not individual draw operations.
  The window keeps the most recent 120 timestamps for an accurate
  short-term reading.
- **Bytes in/out**: Network throughput per channel
- **Latency**: Client-observed inter-PING interval on the main channel,
  in milliseconds. SPICE has no client-originated probe (`SPICE_MSG_PING`
  is server→client only), so ryll cannot measure absolute network RTT.
  Instead, the main-channel PING handler records `Instant::now()` and
  emits the gap to the previous PING as a sample. The number includes
  the server's send cadence and the client's receive turnaround;
  spikes indicate a network or server stall. Sparkline mirrors the
  bandwidth one (60-sample rolling history, amber bars). Implemented
  via `LatencyTracker` in `app.rs`.
- **Bandwidth sparkline**: A rolling 60-sample history of bytes/sec is
  displayed in the status bar as a small bar chart. Channel read loops
  increment a shared `AtomicU64` byte counter; the `BandwidthTracker`
  in `app.rs` samples it once per second and renders the sparkline.
- **Runtime metrics in bug reports**: each bug-report ZIP includes a
  `runtime-metrics.json` file with process and per-thread CPU%, RSS,
  and VmSize sampled over a 2-second window. On **Linux** the data
  comes from `/proc/self/stat`, `/proc/self/status`, and
  `/proc/self/task/*/`. On **macOS** process-level data comes from
  `task_info(MACH_TASK_BASIC_INFO)` and per-thread data from
  `task_threads` + per-port `thread_info(THREAD_BASIC_INFO)` +
  `thread_info(THREAD_IDENTIFIER_INFO)` (for the stable
  64-bit thread id) + `pthread_getname_np` (for the name).
  The Mach port array is held in a `MachThreadList` RAII guard
  so port references are released on every exit path. Other
  platforms emit a graceful "unavailable" payload. The `MacOS`
  enum variant has the same JSON shape as `Linux`, distinguished
  by the `platform` field. Implemented in
  `shakenfist-spice-renderer/src/metrics.rs`.

This instrumentation is the primary purpose of ryll -- measuring kerbside proxy
performance.

## Traffic Ring Buffer

Every SPICE message (sent and received) is recorded in a per-channel
ring buffer regardless of whether `--capture` is active. The ring
buffer retains the most recent traffic up to a 50 MB total cap,
allocated by weight per channel rather than split evenly:

| Channel | Cap | Weight | Coverage at measured desktop rates |
|---------|-----|--------|-------------------------------|
| display | 32 MB | 16 | ~16 s @ 2 MB/s typical, ~5 s @ 6 MB/s peak |
| usbredir | 4 MB | 2 | session-long when idle (active transfers exceed any cap) |
| playback | 4 MB | 2 | many minutes of audio |
| cursor | 4 MB | 2 | many minutes |
| main | 4 MB | 2 | hours |
| inputs | 2 MB | 1 | hours |

Weighting display most heavily is load-bearing for the
notification-snapshot feature: a snapshot captured at notification-fire time covers
the run-up to the event for users to file in a bug report.

Each entry stores structured metadata (channel name, direction,
message type ID and human-readable name, wire and payload sizes,
timestamp) alongside the pcap frame bytes for export. SPICE messages
that exceed the IPv4 single-frame limit (~64 KB) are split into
multiple TCP segments via the shared `capture::segment_payload`
helper; each `TrafficEntry` carries its first segment in `pcap_frame`
and any trailing segments in `additional_segments: Vec<Arc<[u8]>>`.
The `Arc<[u8]>` choice for both fields makes
ring-buffer entry clones O(N atomic refcount bumps) rather than
O(total bytes), which is what enables the notification-snapshot
store's cheap deep-copy at fire time.

The `TrafficBuffers` struct in `ryll/src/bugreport.rs` holds all six
per-channel `TrafficRingBuffer` instances behind `Mutex<>` and is
shared via `Arc<TrafficBuffers>` between all channel handler tasks
and the UI thread. This supports both bug-report export, the live
traffic viewer, and the snapshot-on-notification path. (The webdav
channel is intentionally absent — its handler does not call
`traffic.record_*` today; tracked as a follow-up in the
[dogfooding feedback plan](/components/ryll/plans/PLAN-session-001-feedback/).)

## Channel State Snapshots

Each channel handler maintains an `Arc<Mutex<T>>` snapshot struct
that captures the channel's mutable state. The snapshots are updated
in-place after every batch of processed messages and after every sent
message. All snapshot structs derive `serde::Serialize` so they can be
written to JSON for bug reports.

| Snapshot struct | Channel | Key fields |
|----------------|---------|------------|
| `DisplaySnapshot` | Display | Image cache size/IDs, recent decode results (last 20) with per-decode wall-time, decode/socket/ACK diagnostic counters, pcap writer-queue drop counter, ACK state, bytes in/out, image cache statistics (cap in bytes, LRU eviction count, bytes evicted) from `BoundedImageCache` |
| `InputsSnapshot` | Inputs | Button state, motion count, recent input events (last 50), per-opcode recv/send maps, unknown-opcode counter, pcap writer-queue drop counter, bytes in/out |
| `CursorSnapshot` | Cursor | Cursor cache contents, ACK state, per-opcode recv/send maps, unknown-opcode counter, pcap writer-queue drop counter, bytes in/out |
| `MainSnapshot` | Main | Session ID, mm_time, keepalive, per-opcode recv/send maps, unknown-opcode counter, pcap writer-queue drop counter, bytes in/out |
| `PlaybackSnapshot` | Playback | Per-session audio metadata (`PlaybackSessionInfo`), start/stop counts, data-packet and decode counters, PCM byte counts, recent decode-duration ring (cap 64), device-side atomics (callbacks, underruns, ring overflows, samples consumed), volume/mute/latency params, per-opcode recv/send maps |
| `UsbredirSnapshot` | Usbredir | Redirected device list (`RedirectedDevice`), device connect/disconnect totals with timestamps, server/client capability bitmasks from hello handshake, per-opcode recv/send maps, unknown-opcode counter, bytes in/out |
| `WebdavSnapshot` | Webdav | Transport common + per-opcode recv/send maps (pending) |
| `AppSnapshot` | App (UI) | FPS, bandwidth, surfaces, cursor position, uptime, video encoder-queue drop counter, render-side mpsc-queue lag aggregates for `ImageReady*` and `DisplayMark` events |

All channel snapshots share an eight-field transport common baseline
(`bytes_in`, `bytes_out`, `last_recv_ts_secs`, `last_send_ts_secs`,
`ping_recv_count`, `pong_send_count`, `last_ping_recv_ts_secs`,
`writer_dropped_count`) plus four baseline additions
(`messages_recv_by_opcode`, `messages_send_by_opcode`,
`last_unknown_opcode`, `unknown_opcode_count`). See the
[channel diagnostics audit](/components/ryll/channel-diagnostics-audit/) for the full
audit matrix and minimum-baseline rationale.

The `ChannelSnapshots` struct in `ryll/src/bugreport.rs` holds the four
channel snapshot `Arc<Mutex<T>>` values and is created alongside
`TrafficBuffers` in `run_connection()`. The `AppSnapshot` is
maintained separately by the `RyllApp` event loop.

Updates hold the mutex only briefly (copying a handful of scalars
and small collections), so contention with the UI thread is
negligible.

## Bug Report Assembly

`BugReport` in `ryll/src/bugreport.rs` assembles a self-contained zip
file from the ring buffer, channel snapshots, and app state.  The
zip contains:

```
ryll-bugreport-YYYY-MM-DDTHH-MM-SSZ.zip
├── metadata.json         # report type, description, ryll version,
│                         #   platform, target host/port, timestamp
│                         #   (submit), triggered_at (dialog-open),
│                         #   session_uptime_secs (submit),
│                         #   triggered_uptime_secs (dialog-open)
├── session.json          # AppSnapshot (FPS, bandwidth, surfaces)
├── channel-state.json    # snapshot of the affected channel
├── traffic.pcap          # ring buffer pcap (capture feature only)
├── screenshot.png        # trigger-time full surface (Display only)
├── screenshot-region.png # submit-time crop at the selected region
│                         #   (Display only, when a region was drawn)
└── runtime-metrics.json  # process and per-thread CPU%, RSS, VmSize
                          #   sampled over a 2-second window at
                          #   report-creation time (Linux only;
                          #   non-Linux platforms record
                          #   available:false with a reason)
```

Report types are `Display`, `Input`, `Cursor`, `Connection`, `Usb`,
and `Pedantic`, each mapping to one SPICE channel or the
--pedantic observer path.  `BugReport::new()` samples runtime
metrics over a 2-second window (blocking the caller), then gathers
and serialises all data synchronously.  `BugReport::write_zip()`
writes the zip to the capture directory's `bug-reports/`
subdirectory (if `--capture` is active) or the current working
directory.

`RyllApp::generate_bug_report()` is the high-level entry point
that collects surface pixels, constructs the `BugReport`, and
writes the zip.

Display bug reports carry two PNGs. `screenshot.png` is the
surface captured the moment the dialog opens — a background
`std::thread` PNG-encodes the cloned RGBA while the user types a
description. `screenshot-region.png` (when a region was drawn) is
a crop of the submit-time surface at the selected rectangle,
encoded on the UI thread after the user finishes the drag. The
two images are deliberately different moments in time.

Non-Display submissions drop the precomputed PNG even if one was
captured — the dialog captures unconditionally on open (so an
artefact doesn't fade while the user decides what to submit), but
only includes the PNG when the user actually submits as Display.

## Bug Report Dialog

Pressing **F12** or using **Menu → Report** opens a
centred modal dialog for generating bug reports.  The
dialog contains:

1. A privacy warning about sensitive data in reports.
2. Radio buttons to select the report type (Display, Input,
   Cursor, Connection).
3. An optional description text field.
4. Capture and Cancel buttons.

While the dialog is open, keyboard and mouse input is not forwarded
to the SPICE server.  F12 is always consumed by ryll (never sent to
the guest).  Escape closes the dialog.

The dialog uses a **two-pass pattern** to avoid egui borrow checker
conflicts: the UI is rendered in a closure that collects the user's
action into a local variable, then the action is executed on `self`
after the closure returns.

After a successful report, a transient status message ("Bug report
saved to ...") is displayed in the status bar for 5 seconds.

### Display region selection

When the user selects "Display" and clicks "Capture", the dialog
closes and the app enters **region selection mode**:

1. A translucent instruction banner appears at the top of the
   surface: "Click and drag to select the affected region.
   Press Escape to skip."
2. The OS cursor changes to a crosshair (the SPICE cursor overlay
   is hidden).
3. The user drags a rectangle; a translucent red overlay shows
   the selection.
4. On mouse release, the report is generated with the region
   coordinates in the metadata.
5. Pressing Escape skips selection and generates without a region.

### Trigger-time snapshot

On dialog open, `RyllApp::begin_trigger_snapshot` clones the
largest surface's RGBA and spawns a named `std::thread`
(`ryll-bugreport-png`) that PNG-encodes into a shared
`Arc<Mutex<Option<Result<Vec<u8>>>>>`. The submit path
(`finish_bug_report` → `take_trigger_for_submit`) consumes the
encoded bytes via `try_lock`, falling back to a live encode if
the encoder hasn't finished. Close-without-submit paths (Escape,
Cancel, F12 toggle-off) drop the `Arc`; the thread finishes into
what becomes garbage.

Keyboard and mouse input is not forwarded to the SPICE server
during selection.  Coordinates are clamped to the surface bounds.

## Live Traffic Viewer

Pressing **F11** or using **Menu → Traffic** toggles a
right-side panel showing a live feed of recent SPICE
protocol messages from the ring buffer.

The viewer collects entries from all four channels via
`TrafficBuffers::recent_view_entries()`, which returns lightweight
`TrafficViewEntry` structs (no pcap frame data).  Entries are cached
in `RyllApp` and refreshed every 250ms to minimise mutex contention.

Features:
- **Channel filters**: checkboxes to hide/show individual channels
- **Pause/Resume**: freezes the display for inspection
- **Auto-scroll**: sticks to the bottom when not paused
- **Colour-coded channels**: main=blue, display=green, inputs=orange,
  cursor=purple

Each row shows: relative timestamp, channel name, direction arrow
(sent/received), message name, and wire size.

F11 is consumed by ryll and not forwarded to the SPICE server.


## Capture Mode

When `--capture <DIR>` is specified, ryll records:

### Session metadata

`metadata.json` is written at session start with platform details
(OS, architecture), ryll version, and connection target (host, port).
This makes capture directories self-describing when shared for bug
reports or debugging.

### Protocol capture (pcap)

Each SPICE channel writes a separate pcap file (`main.pcap`,
`display.pcap`, `cursor.pcap`, `inputs.pcap`, `usbredir.pcap`,
`webdav.pcap`) containing
decrypted SPICE mini-header messages wrapped in fake TCP/IP
headers. Wireshark can open these directly.

Implementation: `capture::PcapChannelWriter` per channel, using
`pcap-file` for pcap output and `etherparse` for header
construction. Packets are recorded in `send()` and the read
loop of each channel handler. Writers use unbuffered I/O (no
`BufWriter`) so every packet hits disk immediately.

Large SPICE messages (e.g. uncompressed display updates) can
exceed the IPv4 maximum packet size (65535 bytes). The pcap
writer splits these into multiple TCP segments with sequential
sequence numbers, so Wireshark can reassemble them and the
pcap file never triggers a length-overflow panic.

### Display capture (video)

`display.mp4` contains an H.264 encoded video of the primary
surface (surface 0). Frames are emitted on MARK boundaries
with real timestamps for variable-rate playback.

Implementation: `capture::VideoWriter` is owned exclusively by
a dedicated tokio `video_writer_task` spawned in
`CaptureSession::new`. The
egui call site (`CaptureSession::frame`) is a non-blocking
`try_send` into a bounded mpsc (cap `VIDEO_QUEUE_CAPACITY = 8`)
that returns `bool`; `false` signals the encoder queue was
full and the frame was dropped. The drop counter lands in
`AppSnapshot::video_drop_count`. The encoder task lazy-inits
`VideoWriter` from the first surface-0 frame, encodes RGBA →
YUV420 → H.264 via `openh264`, and muxes into MP4 via the
`mp4` crate. On sender drop the task drains, calls
`VideoWriter::close()` to write the MP4 moov atom, then
exits. Pcap follows the same pattern via `pcap_writer_task`
with cap `PCAP_QUEUE_CAPACITY = 1024`.

The capture session is `Arc<CaptureSession>` shared across all
channels and the app. When `--capture` is not specified, the
field is `None` and all capture code paths are skipped. The
`CaptureSession` uses an `AtomicBool` guard to ensure `close()`
is idempotent -- it may be called both explicitly during
shutdown and again from the `Drop` implementation. `close()`
is **synchronous** and drops both writer senders without
awaiting drain; the tokio tasks finalise (pcap flush is a
no-op since pcap I/O is unbuffered; MP4 writes the moov atom)
on the runtime. This means MP4 finalisation is no longer
synchronous with `close()` — a bug report assembled within
milliseconds of close may see an unfinalised MP4. The
[troubleshooting guide](/components/ryll/troubleshooting/) records the
trade-off rationale.


### Auto-snapshot on channel disconnect

Because oVirt and Kerbside hand out one-time-use SPICE
tickets, any channel that disconnects mid-session is
permanently lost — the client cannot reconnect that
channel without a fresh ticket. To make those events
diagnosable after the fact, every `ChannelEvent::Error`
or `ChannelEvent::Disconnected` triggers a best-effort
auto-snapshot via `BugReport::write_disconnect`. The
zip carries a `disconnect-cause.json` record alongside
the usual pcap / channel-state / metadata files. The
record names the channel that fired, captures whether
the main-channel client-side keepalive timeout fired
(distinguishing "we timed ourselves out" from a server
RST), and embeds a per-channel diagnostics map so a
maintainer can compare the dropped channel against the
others' last-known traffic state.

A 60 s cooldown bounds disk usage during a disconnect
storm; the cooldown is updated even on write failure so
a misconfigured output directory does not retry on every
disconnect event. Snapshots land in (in order of
preference) `--bug-report-dir`, `<--capture>/bug-reports/`,
or the current working directory. The same resolution
chain is used for the manual F12 / Menu → Report path.
Runtime metrics
are recorded as unavailable in this path — sampling
them on the GUI thread would freeze the UI for ~1 s,
and the pcap and channel snapshots are the load-bearing
diagnostic data anyway.

### Notification snapshots and the "file from notification" button

Every notification entry in the side panel carries a
per-row "File…" button. Clicking always
produces a bug-report zip; the variant depends on whether
a live snapshot of the traffic-buffer state exists for
that notification.

On every `RyllApp::push_notification` call, after the
`NotificationStore::push` returns the entry id, the app
captures a cheap deep-copy of `TrafficBuffers` keyed by id.
The clone is O(N atomic refcount bumps) thanks to the
`Arc<[u8]>` for `pcap_frame` and the
`Vec<Arc<[u8]>>` for `additional_segments`. The store is
bounded: at most 5 active snapshots, 60 s TTL. Oldest is
evicted on overflow; expired entries are pruned both at
capture time and once per second from the GUI tick.

When the user clicks the button:

- If `notification_snapshots.take(id)` returns a snapshot,
  the report uses it and metadata.json records
  `report_type.snapshot_state: "AtFire"`. The zip filename
  includes `atfire`. The pcap reflects the ring contents
  from the moment the notification fired.
- If the snapshot is missing (expired or evicted), the
  report uses the current `TrafficBuffers` and tags
  `PostEventOnly` / `postevent`. Useful, but without the
  run-up to the event.

### Auto-snapshot mode (`--auto-snapshot-interval`)

A flight-data-recorder mode. When `--auto-snapshot-interval N`
is set, a background tokio task fires a complete bug report
every N seconds into `<bug-report-dir>/auto-snapshots/`. This
captures full session state regardless of whether the operator
notices a symptom in real time — useful for intermittent issues
like audio silences that last only 30 seconds.

Key design points:

- `BugReportType::AutoSnapshot` — a new variant whose
  `channel_name()` returns `"all"`. This causes `BugReport::
  assemble` to embed a merged JSON object in `channel-state.json`
  containing every channel's snapshot (display, inputs, cursor,
  main, playback, usbredir, webdav). The traffic pcap also covers
  all channels via `TrafficBuffers::drain_all_pcap_bytes()`.
- `AutoSnapshotState` in `ryll/src/auto_snapshot.rs` bundles the
  Arc handles the task needs (traffic, channel snapshots, app
  snapshot, notifications, target host/port, output dir, cap,
  interval). All are already Arc-backed on `RyllApp`.
- The task runs in its own std::thread with a dedicated
  `tokio::runtime::Builder::new_current_thread` runtime, spawned
  once on the first `ChannelEvent::SessionInitialized` (a latch
  prevents a second spawn on reconnect).
- A startup `NotifySeverity::Info` notification confirms the mode
  is active: `"Auto-snapshot mode enabled — every {N}s, max {cap}
  snapshots, saving to {path}"`.
- The stats panel renders `"Auto-snapshot: {saved}/{cap}"` when
  the mode is active; the line is hidden when disabled.
- Rolling cap: after each successful write, `prune_to_cap` lists
  `ryll-auto-snapshot-*.zip` in the output dir, sorts
  lexicographically (= chronologically by filename construction),
  and deletes the oldest beyond cap. `auto_snapshots_pruned`
  in `AppSnapshot` tracks the total deleted.
- Write failures: `warn!` always; a `NotifySeverity::Warn`
  notification is pushed at most once per 5-minute cool-down so a
  persistent disk error does not spam the panel. The interval task
  continues regardless.
- Filename scheme:
  `ryll-auto-snapshot-2026-05-18T20-37-42Z-T+47.3s.zip` —
  UTC ISO timestamp (colons replaced with hyphens for Windows
  filesystem portability) plus session uptime suffix.

The button's visual state reflects which path a click would
take: weak/dimmed text + a "snapshot expired" hover tooltip
when no live snapshot exists. `BugReport::write_notification`
mirrors `write_disconnect`'s zip shape — same set of files,
different filename prefix and metadata.

