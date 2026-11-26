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
- **RTT**: Round trip on the inputs channel, in milliseconds, shown in
  the status bar as `RTT` once the pointer has moved. SPICE has no
  client-originated probe (`SPICE_MSG_PING` is server→client only), but
  the server acknowledges every fourth pointer message with
  `MOUSE_MOTION_ACK`. The inputs channel times each bunch from sending
  its fourth message to the ack for it (`MotionRoundTrip` in
  `shakenfist-spice-renderer/src/channels/inputs.rs`) and emits
  `ChannelEvent::InputRoundTrip`. It includes the server's time to read
  and answer, and is only sampled while the pointer moves. Recorded as
  `last_input_rtt_ms` in `session.json`, `last_round_trip_ms` in the
  inputs channel's state, and the control socket's `input_rtt` event.
- **PING interval**: Client-observed interval between server PINGs on the
  main channel, in milliseconds, shown in the status bar under that
  label. It is not a round trip: the main-channel PING handler records
  `Instant::now()` and emits the gap to the previous PING as
  `ChannelEvent::PingInterval`. The number is mostly the server's send
  cadence: about 30 000 ms against andris, which pings every 30 s.
  Spikes beyond the cadence indicate a network or server stall.
  Sparkline mirrors the bandwidth one (60-sample rolling history, amber
  bars). Implemented via `LatencyTracker` in `ryll/src/app/stats.rs`,
  and recorded as `last_ping_interval_ms` in `session.json`.
- **Bandwidth sparkline**: A rolling 60-sample history of bytes/sec is
  displayed in the status bar as a small bar chart. Channel read loops
  increment a shared `AtomicU64` byte counter; the `BandwidthTracker`
  in `ryll/src/app/stats.rs` samples it once per second and renders the sparkline.
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

### Runtime metrics on macOS: why it is built this way

The macOS sampler in `shakenfist-spice-renderer/src/metrics.rs` mirrors
the Linux one (snapshot, sleep for the window, snapshot, delta), with the
delta math factored into `macos_math` so it is unit-tested on every
platform even though the Mach calls compile only on a Mac. The choices
that are not obvious from the code:

- **Process CPU comes from one `task_info(MACH_TASK_BASIC_INFO)` call**,
  not from summing the per-thread `THREAD_BASIC_INFO` values. That is
  one syscall per snapshot and parity with Linux's single
  `/proc/self/stat` read. The cost is that `process.cpu_percent` does
  not exactly equal the sum of `threads[].cpu_percent`, through
  sampling skew; Linux behaves the same way.
- **Threads are matched between the two snapshots by
  `thread_identifier_info.thread_id`**, never by Mach port. The port
  numbers `task_threads` returns for the same OS thread differ from one
  call to the next, so matching on them would attribute CPU to the wrong
  thread. This costs two `thread_info` calls per thread per snapshot,
  which is negligible at a typical 20-40 threads.
- **A thread that appears mid-window is reported at 0% CPU, and one
  that vanishes mid-window is omitted**, which is what the Linux
  sampler does; the Mach path also drops a thread whose `thread_info`
  fails because it died between `task_threads` and the per-thread call.
  Output is sorted by `tid` so the JSON is deterministic.
- **`mach_port_deallocate` and `mach_task_self_` are declared in a local
  `extern "C"` block** because the `libc` crate does not expose
  the first on Apple targets and deprecates the `mach_task_self()`
  wrapper in favour of the `mach2` crate. Both are stable Mach ABI; a
  dependency for two items was judged the larger cost.
- **`task_threads` hands back a port right per thread plus the array
  itself**, and ignoring either leaks a reference per thread per sample.
  `MachThreadList` releases both in `Drop`, and its only constructor is
  the struct literal filled directly by the `task_threads` call, so no
  fallible operation sits between allocation and ownership. The
  `Drop` ignores the return codes of the two deallocations: a failure
  there means a corrupt port table, which is outside this module.
  A leak cannot be unit-tested, because observing the port table from
  inside the process needs fragile `mach_port_kobject`-style
  introspection; the soak in
  [macos-metrics-verification.md](/components/ryll/macos-metrics-verification/) is the
  empirical check.
- **Uptime is baselined by `init_at_startup()`**, called first in
  `main()`. The `LazyLock<Instant>` behind it otherwise initialises at
  the first `sample()`, so a report filed shortly after startup would
  read an uptime near zero. A `sample()` made before `init_at_startup()`
  silently defeats the baseline, so keep it ahead of any sampling.
- **A failing Mach call degrades to `Unavailable`** with the call
  named in `reason`, so `sample()` never panics.
- iOS is out of scope: its sandbox restrictions on the Mach APIs would
  need their own design. Per-thread memory and GPU metrics are not
  collected on either platform because neither OS exposes them cheaply.

The plan that built this is retired; [the plans index](/components/ryll/plans/index/)
links its final text.

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
`traffic.record_*` today; [#784](https://github.com/shakenfist/ryll/issues/784).)

Three choices in the sizing are deliberate:

- **Static weights, not memory-scaled.** The 50 MB total is fixed
  rather than sized from system memory, and the weights are not
  exposed as flags: the rebalance buys most of the retention without
  the cost of growing and shrinking rings at run time, and a retune is
  a one-line constant edit. The weights come from session-001's
  display rate (2 MB/s typical, 6 MB/s peak). A bigger display ring
  would also be wasted on reports, since the pcap in a report is held
  to a 24 MB budget (see [Report size](#report-size)).
- **usbredir cannot be rescued by size.** An active USB transfer can
  reach 50 MB/s, so no realistic cap holds more than a fraction of a
  second of one. Its 4 MB covers the idle and control traffic and the
  start of a transfer, which is where the protocol state changes.
- **A message over 64 KB stays one entry.** The extra segments hang
  off the entry rather than becoming entries of their own, so the
  traffic viewer still shows one row per SPICE message and byte-cap
  eviction removes a message whole, never leaving the tail of a
  half-evicted one in the ring. A message's segments share its
  timestamp, as the live `--capture` writer's do. The limit that
  forced segmentation is the IPv4 16-bit length field, not the
  network MTU: before the fix `build_tcp_frame` refused such a
  payload and the ring silently dropped the message from every
  report. The first diagnosis, jumbo frames, was a coincidence.
  `capture::segment_payload` is shared with the live writer so the two
  cannot drift.

`TrafficEntry::pcap_frame` and each additional segment are
`Arc<[u8]>` rather than `Vec<u8>`; wrapping the whole `TrafficEntry`
in an `Arc` instead would also make clones cheap but would force every
consumer through the pointer and break the by-value APIs.
`build_frame` still returns a plain `Vec<u8>` and the entry is sealed
at the two `record_*` call sites, so a future caller that needs a
mutable buffer is not made to copy out of an `Arc`.

## Channel State Snapshots

Each channel handler maintains an `Arc<Mutex<T>>` snapshot struct
that captures the channel's mutable state. The snapshots are updated
in-place after every batch of processed messages and after every sent
message. All snapshot structs derive `serde::Serialize` so they can be
written to JSON for bug reports.

| Snapshot struct | Channel | Key fields |
|----------------|---------|------------|
| `DisplaySnapshot` | Display | Image cache size and 64 most-recent IDs, per-opcode recv/send maps, unknown-opcode counter, stream lifecycle counters (created/destroyed/rejected), recent decode results (last 20) with per-decode wall-time, decode/socket/ACK diagnostic counters, pcap writer-queue drop counter, ACK state, bytes in/out, image cache statistics (cap in bytes, LRU eviction count, bytes evicted) from `BoundedImageCache` |
| `InputsSnapshot` | Inputs | Button state, motion count, recent input events (last 50), per-opcode recv/send maps, unknown-opcode counter, pcap writer-queue drop counter, bytes in/out |
| `CursorSnapshot` | Cursor | Cursor cache contents, ACK state, per-opcode recv/send maps, unknown-opcode counter, pcap writer-queue drop counter, bytes in/out |
| `MainSnapshot` | Main | Session ID, mm_time, keepalive, per-opcode recv/send maps, unknown-opcode counter, pcap writer-queue drop counter, bytes in/out, guest-agent token and reply counters, display-resize requests and the ones the guest never applied |
| `PlaybackSnapshot` | Playback | Per-session audio metadata (`PlaybackSessionInfo`), start/stop counts, data-packet and decode counters, unsupported-codec drop counter, PCM byte counts, recent decode-duration ring (cap 64), device-side atomics (callbacks, underruns, platform xruns, ring overflows, samples consumed), output device in use (`PlaybackOutputInfo`), last output failure and failure/rebuild/reroute counters, server volume/mute/latency params, per-opcode recv/send maps |
| `UsbredirSnapshot` | Usbredir | Redirected device list (`RedirectedDevice`), device connect/disconnect totals with timestamps, server/client capability bitmasks from hello handshake, per-opcode recv/send maps, unknown-opcode counter, bytes in/out |
| `WebdavSnapshot` | Webdav | Transport common + per-opcode recv/send maps (pending) |
| `AppSnapshot` | App (UI) | FPS, bandwidth, surfaces, cursor position, uptime, video encoder-queue drop counter, render-side mpsc-queue lag aggregates for `ImageReady*` and `DisplayMark` events, ryll's own volume and mute (`null` where no GUI maintains the snapshot) |

All channel snapshots share an eight-field transport common baseline
(`bytes_in`, `bytes_out`, `last_recv_ts_secs`, `last_send_ts_secs`,
`ping_recv_count`, `pong_send_count`, `last_ping_recv_ts_secs`,
`writer_dropped_count`) plus four baseline additions
(`messages_recv_by_opcode`, `messages_send_by_opcode`,
`last_unknown_opcode`, `unknown_opcode_count`), produced by the
shared `OpcodeCounters` type. Only opcodes the protocol crate has a
name for get their own map entry — a server-chosen opcode this build
does not recognise is folded into `unknown_opcode_count` so the map
cannot be grown from the wire. See the
[channel diagnostics audit](/components/ryll/channel-diagnostics-audit/) for the full
audit matrix and minimum-baseline rationale.

`MainSnapshot` also records four fields for diagnosing a stalled UI:
`events_dropped_count`, `events_dropped_by_kind` and
`last_event_drop_ts_secs` count the `ChannelEvent`s the main channel
discarded because the renderer's event queue stayed full for longer than
`MAIN_EVENT_SEND_TIMEOUT` (per `ChannelEvent` variant name, with the
session-relative time of the latest drop); a non-zero count means the UI
stopped draining events. Mouse mode and vdagent state are not events, so
they are never counted here: the main channel publishes them through the
renderer's `SessionState` watch channels, which hold the latest value and
cannot drop it. `server_mouse_mode` is the mouse mode the server
last announced in `MAIN_INIT` or `MOUSE_MODE` (1 = server/relative,
2 = client/absolute); compare it with `mouse_mode` in the app snapshot, as a
mismatch means the client is sending the wrong kind of pointer message.

The `ChannelSnapshots` struct in `ryll/src/bugreport.rs` holds the four
channel snapshot `Arc<Mutex<T>>` values and is created alongside
`TrafficBuffers` in `run_connection()`. The `AppSnapshot` is
maintained separately by the `RyllApp` event loop.

Updates hold the mutex only briefly (copying a handful of scalars
and small collections), so contention with the UI thread is
negligible.

### Diagnosing "video not keeping up": how the instrumentation is shaped

A report saying the video stream is not keeping up has to say which of
server, network, decode, render or ryll's own capture I/O was the
bottleneck without the session being re-run. How to read the fields is in
[troubleshooting.md](/components/ryll/troubleshooting/#reading-display-channel-statejson-for-video-not-keeping-up);
this records why they are the fields they are. The measurement came
first, deliberately: the cheap signals say whether any threading change
is worth making, and the capture writers moved off the hot paths only
where inline I/O could stall the SPICE socket or the GUI.

- **Decode time shares the `recent_decodes` ring.** Per-decode wall time
  is kept as the last N entries plus min/max/mean computed at
  snapshot-emit time, rather than as a histogram, which would cost state
  for no extra diagnostic answer. Microseconds, not milliseconds, because
  small LZ4 and pixmap decodes finish in well under 1 ms. Cache hits and
  failures that never reach the decoder record zero and are excluded
  from the aggregates.
- **Socket pressure is a fill-rate proxy, not a buffer reading.** A read
  that returns the full 256 KB chunk means the OS receive buffer had at
  least that much waiting. Inspecting the buffer directly (`FIONREAD`)
  was rejected: it needs the raw fd of a tokio stream and a per-platform
  code path, and the proxy answers the question well enough.
- **ACK cadence is a bounded ring of intervals** (the last 32), which at
  a typical ACK window and busy display rates covers the last 10-30
  seconds: enough to see whether ACKs paused, without bloating the
  snapshot. A snapshot of `ack_window`/`last_ack` alone cannot tell a
  healthy cadence from a long pause.
- **Capture writers drop on overflow rather than block.** The pcap and
  MP4 writers sit behind bounded queues, and a full queue drops the item
  and counts it (`writer_dropped_count` per channel, `video_drop_count`
  in `session.json`). Blocking would reintroduce the very back-pressure
  the split exists to remove, at the price of gaps in a pcap or video
  when the disk falls behind, which the counters make visible. Each
  channel counts its own drops, but `webdav` and `playback` have no
  pcap writer: their items are accepted by the queue and discarded by
  the writer, so their counter measures queue pressure only and stays
  zero in practice.
- **Timestamps are taken at enqueue, not at dequeue.** A writer that
  falls behind by hundreds of milliseconds would otherwise compress the
  delayed packets into a burst at the moment it caught up, and the
  timeline in the report would mislead. The same applies to MP4
  presentation timestamps.
- **The channel name in a queue item is a `&'static str`** matched
  against the `CHANNELS` list in `capture.rs`, not an owned `String`
  (an allocation per enqueue, defeating the hot-path gain) and not an
  enum (couples the `CaptureSink` trait surface to the pcap layer).
  A name not in the list is dropped before any queue work.
- **Queue capacities are sized by item weight, not count.** The pcap
  queue holds 1024 small items, but a display chunk can be 256 KB, so
  the nominal worst case is hundreds of megabytes that a pathologically
  slow disk would have to reach. The video queue holds 8 because each
  item is a whole RGBA surface (about 8 MB at 1080p, 33 MB at 4K): at
  the 4K worst case that is roughly 264 MB queued before drops begin,
  which is acceptable for a diagnostic mode. Both are guesses to be
  revisited only if real reports show high drop rates on a healthy disk.
- **Only surface 0 is encoded, and the filter is in the encoder thread.**
  Filtering in `frame()` would still copy the pixels into an `Arc`
  just to discard them; checking on dequeue keeps the enqueue uniformly
  cheap.
- **Per-channel pcap files are independent**, so only within-channel
  order is preserved (the queue is FIFO). A single unified pcap with
  strict cross-channel ordering would need one multiplexed writer; nothing
  has asked for it.
- **Render-side latency is mpsc-queue lag.** `ImageReady*` and
  `DisplayMark` carry `produced_at_secs`, stamped just before the send,
  and the egui event drain subtracts it from the time it picks the event
  up; both clocks are the one `traffic.elapsed()` epoch. "Inside the egui
  paint" timing was rejected as disproportionate (it needs eframe or
  compositor instrumentation), and end-to-end "pixels visible to the
  user" is unmeasurable without external instrumentation. The diagnostic
  pattern is high `image_ready_lag_*` while decode and socket-fill look
  healthy, which points at the GUI thread being held by long synchronous
  work in `App::update`. Events drained in one `process_events` call
  share a `consumed_at`, so their samples are correlated: `max` is the
  informative number and `mean` is biased by batch size. Negative
  differences from clock granularity saturate at zero, and the first
  samples after startup include startup lag until the 32-entry ring
  rotates.
- **MP4 finalisation is asynchronous, on purpose.** See decision 6 in
  [design-decisions.md](/components/ryll/design-decisions/): synchronous finalisation
  would put the caller back to blocking on the encoder draining, which is
  the stall the encoder thread removes.

Nothing is instrumented on the cursor, playback or main channels for this
purpose; the data was only wanted for display. The plan that built these
fields is retired; [the plans index](/components/ryll/plans/index/) links its final
text.

## Bug Report Assembly

`BugReport` in `ryll/src/bugreport.rs` assembles a self-contained zip
file from the ring buffer, channel snapshots, and app state.  The
zip contains:

```
ryll-bugreport-YYYY-MM-DDTHH-MM-SSZ.zip
├── metadata.json         # report type, description, ryll version,
│                         #   platform, target, timestamp
│                         #   (submit), triggered_at (dialog-open),
│                         #   session_uptime_secs (submit),
│                         #   triggered_uptime_secs (dialog-open),
│                         #   traffic_capture (what traffic.pcap
│                         #   holds and what was left out)
├── session.json          # AppSnapshot (FPS, bandwidth, surfaces,
│                         #   held keys and recent key events,
│                         #   ryll's open panels, menus and dialogs)
├── channel-state.json    # snapshot of the affected channel
├── traffic.pcap          # newest ring buffer traffic, within the
│                         #   report's size budget (capture feature
│                         #   only)
├── screenshot.png        # trigger-time full surface (Display only)
├── screenshot-region.png # submit-time crop at the selected region
│                         #   (Display only, when a region was drawn)
├── window-screenshot.png # ryll's whole window as the report's first
│                         #   frame drew it (Ui only)
└── runtime-metrics.json  # process and per-thread CPU%, RSS, VmSize
                          #   sampled over a 2-second window at
                          #   report-creation time (Linux only;
                          #   non-Linux platforms record
                          #   available:false with a reason)
```

Report types are `Display`, `Input`, `Cursor`, `Connection`, `Usb`,
`Audio`, `Ui`, and `Pedantic`, each mapping to one SPICE channel or the
--pedantic observer path. `Connection` covers the main channel only;
an audio problem needs `Audio`, whose `channel-state.json` is the
playback snapshot. `Ui` is for a problem in ryll's own interface (a
panel or menu that opened unasked, say); it carries the inputs
channel's traffic and state, since the interface usually misbehaves
after a key or click.  `BugReport::new()` samples runtime
metrics over a 2-second window (blocking the caller), then gathers
and serialises all data synchronously.  `BugReport::write_zip()`
writes the zip to the capture directory's `bug-reports/`
subdirectory (if `--capture` is active) or the current working
directory.

No caller may run that sample on a thread that has to stay
responsive.  The auto-snapshot loop wraps the whole
`BugReport::new()` call in `tokio::task::spawn_blocking`; the
--pedantic observer samples in `spawn_blocking` and hands the
result to `BugReport::write_pedantic()`.

`RyllApp::finish_bug_report()` is the GUI entry point.  It
captures the submit-time surface pixels, the trigger timestamps,
and the precomputed PNG, starts the metrics sample on a dedicated
`std::thread`, and parks the submission.
`RyllApp::poll_pending_bug_report()` picks it up on the frame the
sample lands, calls `BugReport::assemble()` with the sampled
metrics, and writes the zip.  The window keeps painting throughout,
and a notification announces the start of the capture as well as
its result.  A second submission is refused while one is in flight.

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

### ryll's own interface

The guest-surface PNG cannot show what ryll draws over it, so every
GUI report's `session.json` has a `ui` section, kept by
`UiStateTracker` in `ryll/src/app/ui_state.rs`. At the end of each
frame it is told which panels, menus and dialogs are open
(`notifications_panel`, `menu_or_popup`, `traffic_viewer`,
`usb_panel`, `folders_panel`, `protocol_gaps`, `bug_report_dialog`,
`region_select`, `paste_error_dialog`, `connection_modal`) and what
egui claims of the input. It records:

- `now`: what is open, each with the time it opened, plus
  `egui_wants_keyboard`, `egui_wants_pointer` and `focused_widget`.
  ryll drops egui's keyboard focus every frame unless the bug report
  dialog is open (#535), so `egui_wants_keyboard` should otherwise be
  false.
- `at_report_trigger`: the same, as of the frame before the report's
  dialog appeared, so it shows what the report is about rather than
  the dialog.
- `recent_changes`: the last 64 openings and closings.

Times are seconds since session start, the clock of the key log in
`keyboard`, the inputs channel's `recent_events` and the pcap, so a
panel that opened unasked can be lined up with the key before it.

When a report starts, ryll also asks eframe for a screenshot of the
whole window (`ViewportCommand::Screenshot`). The dialog is not drawn
until the screenshot arrives, normally the next frame and at most
500 ms later, so the screenshot shows the window as the user saw it.
It is PNG-encoded on a worker thread and written as
`window-screenshot.png` in `Ui` reports only.

## Bug Report Dialog

Pressing **F12** or using **Menu → Report** opens a
centred modal dialog for generating bug reports.  The
dialog contains:

1. A privacy warning about sensitive data in reports.
2. Radio buttons to select the report type (Display, Input,
   Cursor, Connection, USB, Audio, and ryll itself).
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

### Report size

A report is only useful if it can be attached to an issue, and
GitHub limits attachments to 25 MB. The display ring alone is 32 MB,
and its payloads are mostly compressed images that the zip can't
shrink (entries are stored, not deflated), so a Display report taken
after a busy minute used to come out at 32 MB.

`BugReport::assemble` therefore builds the pcap last and gives it
what the other files leave of a 24 MB target
(`REPORT_TARGET_BYTES`), less 256 KB for `metadata.json`, any
`disconnect-cause.json` and the zip's headers. It keeps the
**newest** traffic, the run-up to whatever the report is about, and
leaves out the oldest. The cut never falls inside a SPICE message
that was split into several TCP segments. A pcap is never given less
than 4 MB, so a report with an unusually large screenshot can still
come out over the limit. When a report saved from the GUI is over
25 MB, a warning notification says so.

The budget applies to every report: manual, notification,
disconnect, `--pedantic` and auto-snapshot. The ring buffers
themselves are unchanged, and `--capture` still writes complete
per-channel pcaps, which is the way to keep everything.

`metadata.json`'s `traffic_capture` records the result:

| Field | Meaning |
|-------|---------|
| `budget_bytes` | Bytes the pcap was allowed to use |
| `written_bytes` | Size of `traffic.pcap` |
| `packets_written` | TCP segments in the pcap |
| `packets_omitted` | Older segments in the ring that were left out |
| `bytes_omitted` | Frame bytes of those segments |
| `oldest_packet_secs` | Session uptime of the oldest packet kept: the capture runs from here to the report |
| `truncated` | `true` when older traffic was left out |

It is `null` in a build without the `capture` feature, which writes
no pcap, and when the pcap writer failed, which is logged and leaves
`traffic.pcap` out of the report.

Report pcaps and `--capture` pcaps use a 262144-byte snaplen, as
tcpdump does. With pcap-file's default of 65535, the writer refused
the first segment of any SPICE message over 64 KB (a full segment is
65549 bytes with its Ethernet header), so both used to lose part of
every large display message. A `--capture` pcap that still refuses a
frame logs a warning, once per channel.

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
   coordinates in the metadata. A click without a drag stays in
   region selection and says so in a notification.
5. Pressing Escape skips selection and generates without a region.

A region must have strictly positive area, so a deliberate one-pixel
drag is accepted and no jitter threshold is applied, since no data
shows jitter-made regions. The check is in the GUI (`validate_region`)
rather than in `ReportRegion`, because the drag handler is its only
producer, and `encode_region_png` still refuses a zero-area rectangle
behind it. A zero-width region used to be written into `report.json`
with no PNG to match, leaving a reader with a region of interest they
could not find.

The dialog says, while Display is selected, that Capture will ask
for a region, and entering region selection also posts a
notification, because a user who misses the banner otherwise sees
a window that ignores the guest and appears stuck.

Every way out of region selection writes the report, and the log
records which one it was (`app: region selection completed`,
`skipped with Escape`, `abandoned by a reconnect`, `abandoned by a
lost connection` or `abandoned by the window closing`), next to
`app: bug report waiting for region selection` on the way in. A
reconnect or a lost connection clears the surface being selected
from, and closing the window ends the session, so each writes the
report without a region rather than dropping it. Closing the
window also writes a report still waiting for its runtime metrics
sample, after at most a second past the end of its sample window.

Only one report is captured at a time. Pressing Capture on a
Display report while an earlier report is still sampling its
metrics (the two seconds after its Capture) does not enter region
selection: the report is refused at once with "A bug report is
already being captured", so a selection never ends with a report
that cannot be written.

While a selection is in progress, **Menu → Report** and **Menu →
Screenshot** are disabled, as F12 and F8 are, and the USB and
connection error dialogs' report buttons are refused with a
notification: opening a second report would replace the pending
one's trigger snapshot.

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

### Why a report is built this way

The choices behind the bug report path, kept so that a change to it
does not re-propose something already weighed.

- **One zip, not a directory of loose files.** A report exists to be
  attached to an issue or an email, so it is a single file. Entries
  are stored rather than deflated: the pcap payloads and the PNGs are
  already compressed.
- **The ring buffer is always on and has no flag.** A report taken
  after the fact is only useful if the traffic was already being kept,
  and 50 MB is small beside what the client otherwise uses, so
  `--capture` is for recording a whole session and not a precondition
  for reporting. Entries hold already-built pcap frames, built by the
  `capture` module's frame code, so a report writes them out without
  re-deriving anything. Each channel has its own buffer and mutex, so
  a busy display channel cannot evict a quiet channel's history (the
  split is weighted, above). A lock-free structure was judged
  premature: a lock is held for one push, or for the traffic viewer's
  250 ms refresh.
- **Channels publish their snapshots; the UI never asks for them.** A
  channel handler runs on a different thread from the UI, and a
  request/response round trip through the event channel would need
  cross-thread coordination. Each handler instead updates its
  `Arc<Mutex<T>>` in place and the report reads the latest copy.
- **The PNG is the whole surface and the region is metadata.** The
  developer can crop locally. This is why the dialog warns that a
  report contains screen contents. `screenshot-region.png` was added
  later as a second image, not a replacement. The region is picked by
  dragging a rectangle rather than clicking a point.
- **F11 and F12 never reach the guest.** A guest rarely needs either,
  and the report shortcut is worth more than the keystroke.

The trigger-time snapshot has constraints of its own:

- **Capture on open, whatever type is selected.** The artefact may be
  gone by the time the user has chosen a type and typed a description;
  that delay is the whole reason the snapshot exists. Do not guard
  the capture on the selected type: a dialog opened as Input and
  switched to Display would then have missed the moment. The PNG is
  *included* only for a Display submission, so Input, Cursor and the
  other types are no larger than before. The wasted encode on a
  non-Display submission is accepted, since dialogs open rarely and
  the encode is off the UI thread.
- **A plain `std::thread`, not `tokio::task::spawn_blocking`.** PNG
  encoding is CPU-bound with nothing async in it, and belongs outside
  the tokio runtime that drives the SPICE channels.
- **The surface is cloned, with no temporary file.** A 1024x768 RGBA
  surface is about 3 MB and a 2560x1600 one about 16 MB, held only
  from the trigger until the encode finishes (well under a second on a
  modern CPU, while the user types), after which only the compressed
  bytes remain. If profiling ever shows the clone matters, revisit it;
  it was weighed with the operator on 2026-04-23 and judged not worth
  a temp file.
- **The submit path never blocks on the encoder.** `try_lock` with a
  live-encode fallback means a user who clicks Capture before the
  worker is done gets a slightly later image, not a frozen window. The
  fallback also covers a failed thread spawn and a report triggered
  before any surface existed (which then has no PNG either, but still
  has its timestamps). Cancelling needs no primitive: dropping the
  `Arc` leaves the worker to finish into garbage.
- **`screenshot-region.png` crops the submit-time surface, never the
  trigger-time PNG.** The region's coordinates are in the surface
  space of the moment the user drew it, and the guest may have resized
  in between, so re-cropping the trigger-time image would need a
  decode, a coordinate mapping and a re-encode, and would be wrong
  whenever the size changed. `region` in `metadata.json` therefore
  indexes `screenshot-region.png` as a whole file, and indexes
  `screenshot.png` only if the surface had the same geometry at both
  moments. The crop is clamped to the surface (right and bottom are
  exclusive), and a rectangle that is empty after clamping produces no
  image rather than an error.
- **The new metadata fields were added, not substituted.**
  `session_uptime_secs` keeps meaning uptime at submit, and
  `triggered_at` / `triggered_uptime_secs` sit beside it, so a reader
  of an older report still finds the keys it knew. The two are always
  present, never `null`: a report with no dialog behind it takes the
  submit values, so tooling need not special-case them. `--pedantic`
  reports are the example, since the observer firing is itself the
  moment of interest and gets no trigger-time image or region.

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
(OS, architecture), ryll version, and a single `target` field (from
`ConnectionConfig::display_target()`, so a tunnelled connection's
proxy address is shown with the target redacted rather than the raw
`host:port`). For a direct connection `target` is `host:port` with
the port actually dialled, which is the TLS port when the `.vv` has
one; `target_tls_port` records that TLS port on its own, and is null
for a plain connection. Bug-report zips carry the same `target`
field in their `metadata.json`. This makes capture directories self-describing when
shared for bug reports or debugging.

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

Clipboard text crosses the main channel in `AGENT_DATA` messages,
so whatever is copied between host and guest is in `main.pcap`, and
in the main channel's ring buffer that bug reports include. The GUI
has always synced the clipboard. Headless and web sessions do so
only when they run a control socket (`--control-socket`), whose
clipboard verbs need it; such a session fetches every guest copy at
once, so guest clipboard text can appear in their captures and bug
reports too, as can text set with `clipboard_set`. Without a control
socket they never request the guest's clipboard.

Large SPICE messages (e.g. uncompressed display updates) can
exceed the IPv4 maximum packet size (65535 bytes). The pcap
writer splits these into multiple TCP segments with sequential
sequence numbers, so Wireshark can reassemble them and the
pcap file never triggers a length-overflow panic.

### Display capture (video)

`display.mp4` contains an H.264 encoded video of the primary
surface (surface 0). Frames are emitted on MARK boundaries
with real timestamps for variable-rate playback. Only the GUI
records it: frames come from the egui frame loop, and headless
mode, which paints no surface, writes the pcaps alone.

Implementation: `capture::VideoWriter` is owned exclusively by
a dedicated OS thread (`ryll-capture-video`, running
`video_writer_loop`) started in `CaptureSession::new`. The
egui call site (`CaptureSession::frame`) is a non-blocking
`try_send` into a bounded channel (cap `VIDEO_QUEUE_CAPACITY = 8`)
that returns `bool`; `false` signals the encoder queue was
full and the frame was dropped. The drop counter lands in
`AppSnapshot::video_drop_count`. The encoder thread lazy-inits
`VideoWriter` from the first surface-0 frame, encodes RGBA →
YUV420 → H.264 via `openh264`, and muxes into MP4 via the
`mp4` crate. On sender drop the thread drains, calls
`VideoWriter::close()` to write the MP4 moov atom, then
exits. Pcap follows the same pattern on `ryll-capture-pcap`
(`pcap_writer_loop`) with cap `PCAP_QUEUE_CAPACITY = 1024`.

The writers are threads rather than tokio tasks on purpose.
`main()` creates the `CaptureSession` before any tokio runtime
exists, and in GUI mode one session outlives every
per-connection runtime (each reconnect builds a new one), so
the writers cannot belong to a runtime. Their work is blocking
file I/O and encoding in any case.

The capture session is `Arc<CaptureSession>` shared across all
channels and the app. When `--capture` is not specified, the
field is `None` and all capture code paths are skipped. The
`CaptureSession` uses an `AtomicBool` guard to ensure `close()`
is idempotent -- it may be called both explicitly during
shutdown and again from the `Drop` implementation. `close()`
does not block: it drops both writer senders and returns, and
the writer threads finish on their own (pcap flush is a no-op
since pcap I/O is unbuffered; MP4 writes the moov atom).
`close_and_wait()` also waits for both threads to exit; the
exit paths in `main` call it so the MP4 is finalised before
the process exits, even while a detached GUI connection
thread still holds a clone of the session. The wait gives up
with a warning after five seconds, so a wedged encoder or a
stalled disk leaves an unfinalised MP4 rather than a process
that will not exit. The egui update path only ever calls the
non-blocking `close()`. Separately, `Drop` waits the same way,
so the MP4 is also finalised once the last
`Arc<CaptureSession>` is gone. MP4 finalisation is still not
synchronous with `close()` itself — a bug report assembled
within milliseconds of close may see an unfinalised MP4. The
[troubleshooting guide](/components/ryll/troubleshooting/) records the
trade-off rationale.

### Why capture records what it does

These choices were weighed when capture was written, and each
rules out an obvious-looking alternative:

- **Decrypted payloads, not TLS bytes.** The pcaps hold the SPICE
  mini-header messages after TLS is removed. The encrypted stream
  is no use for protocol debugging, and the channel handlers only
  ever see decrypted data anyway.
- **Fake TCP/IP framing, not a raw link type.** Wireshark's TCP
  dissector, and with it stream reassembly and Follow TCP Stream,
  only engage on TCP, and the headers are cheap to build. Each
  channel is its own connection (a distinct client port, 10000 plus
  the channel type, to server port 5900), sent and received packets
  use the two directions, and sequence numbers advance by the
  payload length.
- **One pcap per channel,** so a reader debugging the display
  channel opens one file rather than filtering a merged one.
- **One video, of surface 0 only.** If the guest creates further
  surfaces the capture skips them rather than compositing them or
  writing a video each. A mid-session change of the primary
  surface's dimensions stops recording with a warning: the H.264
  encoder and the MP4 track are fixed at the first frame's size.
- **A video frame per MARK, not per draw.** MARK is the display
  channel's frame boundary, and it was confirmed to arrive from
  both a local QEMU and real servers through kerbside, so a frame
  is emitted when the picture is coherent and idle periods cost
  nothing. Frames carry real timestamps, so playback runs at
  session speed. A frame for every draw_copy, to show intermediate
  tile states, was left out until something needs it.
- **H.264 in MP4 via `openh264` and the `mp4` crate,** both of which
  build inside the Docker devcontainer with no system dependency
  (`openh264` bundles Cisco's library, `mp4` is pure Rust).
  Rejected: `minimp4`, because its bindgen needs `libclang`, which
  the devcontainer lacks; raw `y4m`, because the files are huge and
  the frame rate fixed; and `rav1e`, because AV1 encoding is too
  slow. `pcap-file` and `etherparse` are pure Rust too.
- **The frame comes from the egui frame loop,** on `DisplayMark`,
  because the composited surface lives in the app and not in the
  display channel. That is why headless mode records no video.

The retired
[capture plan](https://github.com/shakenfist/ryll/blob/6626d4c560b8e672ae913f3e21a16e83fd7fa596/docs/plans/PLAN-capture.md)
holds the original design text.


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
record names the channel that fired and says how its
connection ended in `disconnect_kind`:

| `disconnect_kind` | Meaning |
|---|---|
| `ServerClosed` | A read returned no bytes: the server closed the socket cleanly |
| `ServerAnnounced` | The server sent the main channel's `DISCONNECTING`; the main channel's end after one is reported this way too, however it ended |
| `KeepaliveTimeout` | The main channel heard nothing for its keepalive timeout, and ryll gave up |
| `Error` | The channel failed; `error_message` says how (a reset, for example) |
| `ConnectFailed` | A connection attempt failed |

It also captures whether the main-channel client-side
keepalive timeout fired, and embeds a per-channel
diagnostics map so a maintainer can compare the dropped
channel against the others' last-known traffic state.
Each entry's `state` is `ended` (with `ended_by`, a
`disconnect_kind`, and `ended_at_secs`), `open` (no end
reported yet), or `no_traffic` (no end reported and
nothing ever received, usually a channel the server never
offered). The report is written once the UI frame's
queued channel events have been handled, so every channel
whose end reached the app in that frame shows as ended,
not just the one that named the report. A channel that
noticed the same failure a few milliseconds later, in
the next frame, still shows as `open`: read `open` as
"no end reported when the report was written", not as
"survived the disconnect". ryll sends no
application-level keepalive on any channel: an idle
channel is kept alive by TCP keepalive on the socket and
by answering the server's PINGs.

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

The button is always present, and filing from a stale notification
still produces a report, because the ring buffer decides how much of
the run-up survives. It is byte-capped, not time-capped, so at 2 to
6 MB/s the display ring held only 1.5 to 4 s before it was
rebalanced, while the low-rate channels keep the whole session. The
dimmed button and the `PostEventOnly` marker tell the reader which
case they have, so a missing run-up is never mistaken for a quiet
one. A notification that folds into an existing one inside the 30 s
dedup window returns the same id, and its snapshot is replaced with
the newer one, matching the folded entry's refreshed timestamp. The
5-entry, 60 s policy applies to every severity and source, is not
configurable, and does not filter which notifications get a button. A
notification report's channel state is the main channel's, as for any
report that names no channel; the disconnect report's per-channel map
is the richer form. The display surface is not snapshotted at fire
time, because that would copy the whole RGBA surface on every
notification.

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
  main, playback, usbredir, webdav). The traffic pcap merges all
  channels via `TrafficBuffers::drain_all_pcap_within()`, keeping
  the newest traffic that fits the report's size budget (see
  [Report size](#report-size)).
- `AutoSnapshotState` in `ryll/src/auto_snapshot.rs` bundles the
  Arc handles the task needs (traffic, channel snapshots, app
  snapshot, notifications, target, output dir, cap,
  byte cap, interval). All are already Arc-backed on `RyllApp`.
- The task runs in its own std::thread with a dedicated
  `tokio::runtime::Builder::new_current_thread` runtime, spawned
  on `ChannelEvent::SessionInitialized`. `reconnect()` replaces
  the traffic and channel-snapshot Arcs wholesale, so each
  session gets its own task: the app raises the outgoing task's
  cancel flag at disconnect and again before spawning the
  replacement, so at most one auto-snapshot task is *running* at
  a time. Nothing joins those threads. Every retire runs on the
  egui UI thread, and a thread already inside a zip write does
  not observe the cancel flag until it finishes, so a join would
  freeze the GUI for seconds. Outgoing handles are held in a
  retiring set instead, reaped non-blockingly with
  `JoinHandle::is_finished`, and a session declines to spawn at
  all once more than four are still winding down (logged at
  `warn!`). If the dedicated runtime cannot be built, the mode
  simply does not start for that session and logs a warning.
- Two independent bounds keep a hostile server from driving
  respawns. A repeated `SPICE_MSG_MAIN_INIT` naming the session
  already in hand is discarded as a duplicate. Because the
  session id is server-chosen, that check alone is defeated by
  alternating ids, so a second guard ignores any
  `SessionInitialized` arriving less than five seconds after the
  last accepted one while still connected. A genuine re-link
  without a visible disconnect is rare enough to tolerate the
  delay; the cost to a hostile peer is one respawn per window
  rather than one per 36-byte message.
- A startup `NotifySeverity::Info` notification confirms the mode
  is active: `"Auto-snapshot mode enabled — every {N}s, max {cap}
  snapshots / {byte cap} MiB, saving to {path}"`.
- The stats panel renders `"Auto-snapshot: {saved}/{cap}"` when
  the mode is active; the line is hidden when disabled.
- Rolling cap: after each successful write, `prune_to_cap` lists
  `ryll-auto-snapshot-*.zip` in the output dir, sorts
  lexicographically (= chronologically by filename construction),
  and deletes the oldest until both budgets are satisfied — at
  most `--auto-snapshot-cap` files *and* at most
  `DEFAULT_AUTO_SNAPSHOT_BYTE_CAP` (512 MiB) of them. The byte
  budget exists because the count alone does not bound disk use:
  `traffic.pcap` is stored uncompressed and merges all six rings,
  so a busy session's zip approaches the 50 MiB ring budget and
  20 of those are roughly a gigabyte. `auto_snapshots_pruned` in
  `AppSnapshot` tracks the total deleted.
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

