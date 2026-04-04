# Bug reporting mode for ryll

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Message parsing | PLAN-thing-phase-01-parsing.md | Not started |
| 2. Decompression | PLAN-thing-phase-02-decomp.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll users encounter bugs — especially display corruption —
that are difficult to report because the relevant information
is visual, transient, and spread across multiple protocol
channels. Currently, the `--capture` flag records full pcap
and video streams, but these have no way to highlight the
*specific moment or region* where the user saw a problem. A
user encountering a glitch has to:

1. Notice the problem
2. Hope that `--capture` was enabled
3. Share the entire capture directory
4. Try to describe the issue in words (e.g. "there was
   corruption in the top-left corner")

This is clumsy for the user and gives the developer
incomplete context. Different kinds of bugs require
different supporting evidence — display corruption needs
pixel snapshots and GLZ dictionary state, input bugs need
modifier key state, future USB redirect bugs will need
device state.

We observed real display corruption during macOS testing
sessions but were unable to provide meaningful debugging
information because the corruption is visual in nature.

## Mission and problem statement

Add an interactive bug reporting feature to ryll that lets
users capture a self-contained, timestamped snapshot of
the client's state at the moment they observe a problem.

The bug report should:

1. Be triggered from the GUI (status bar button or keyboard
   shortcut)
2. Support **multiple bug report types** based on which
   channel the user believes is affected
3. Capture **channel-specific state** relevant to debugging
   that channel
4. Include the **last ~30 seconds of protocol traffic** for
   the affected channel
5. For display bugs, allow the user to **highlight a region**
   of the screen that appears incorrect
6. Bundle everything into a **single zip file** for easy
   sharing
7. Work regardless of whether `--capture` mode is active
   (bug reports are always available, capture is for
   full-session recording)

### Bug report types and per-channel state

| Report type | Channel | Channel-specific state |
|-------------|---------|----------------------|
| Display / visual | Display | PNG screenshot of full surface, user-highlighted region coordinates, GLZ dictionary summary (entry count, image IDs, total memory), last N image decode results (image type, dimensions, cache hit/miss), surface metadata (ID, dimensions, dirty flag) |
| Input | Inputs | Current modifier key state, button\_state bitmask, motion throttle count, last N key/mouse events with scancodes and timestamps |
| Cursor | Cursor | Cursor cache summary (entry count, IDs, dimensions), current cursor position/visibility, current cursor shape metadata (type, dimensions, hot spot) |
| Connection | Main | Session ID, server init parameters, channel list, mouse mode, last ping/pong latency |

All report types also include:

- `metadata.json` — ryll version, platform, target host/port,
  timestamp, report type, user-supplied description
- Last ~30 seconds of pcap traffic for the selected channel
- Session statistics (FPS, bandwidth, latency, uptime)

### Technology choice: zip output

Use the `zip` crate (pure Rust, well-maintained,
MIT-licensed) to bundle the report into a single
`.ryll-bugreport.zip` file. This avoids requiring the user to
manage a directory of loose files and makes it trivial to
attach to a bug report or email.

## Open questions

1. ~~**Where should bug reports be written?**~~ **Resolved**:
   If `--capture` is active, write to a `bug-reports/`
   subdirectory within the capture dir. Otherwise write to
   the current working directory. File name includes
   timestamp: `ryll-bugreport-2026-04-03T12-34-56Z.zip`.

2. ~~**How much traffic to buffer?**~~ **Resolved**: Buffer
   already-constructed pcap frames in a `VecDeque` ring
   buffer, capped at a configurable memory limit (default
   50 MB across all channels, with per-channel proportional
   limits). Reuses `PcapChannelWriter`'s existing frame
   construction code. When `--capture` is active, the ring
   buffer shares the same constructed frames (write to disk
   AND push to the ring buffer).

3. ~~**Region highlighting for display bugs — drag selection
   or click-based?**~~ **Resolved**: Drag selection. A
   translucent instruction banner ("Click and drag to select
   the affected region — press Escape to skip") is shown at
   the top of the surface while in selection mode. The
   selection is drawn as a translucent red overlay while
   dragging.

4. ~~**Should we capture the full surface pixels or just the
   highlighted region?**~~ **Resolved**: Full surface as PNG,
   with region coordinates in the JSON metadata. The
   developer can crop locally. The bug report dialog
   includes a privacy warning before capture (see below).

5. ~~**Optional user description?**~~ **Resolved**: Yes,
   include a text input in the bug report dialog. The
   description is included in the metadata JSON. Allow the
   user to skip it (empty string is fine).

6. ~~**Should the ring buffer always be active?**~~
   **Resolved**: Always active with a 50 MB default. Modern
   machines have plenty of memory and ryll is otherwise
   conservative. No CLI flag needed — just always buffer.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Ring buffer infrastructure | [PLAN-bug-reports-phase-01-ring-buffer.md](/components/ryll/plans/PLAN-bug-reports-phase-01-ring-buffer/) | Complete |
| 2. Channel state snapshots | [PLAN-bug-reports-phase-02-channel-state.md](/components/ryll/plans/PLAN-bug-reports-phase-02-channel-state/) | Complete |
| 3. Bug report assembly and zip output | [PLAN-bug-reports-phase-03-zip-output.md](/components/ryll/plans/PLAN-bug-reports-phase-03-zip-output/) | Complete |
| 4. GUI: report button and description dialog | [PLAN-bug-reports-phase-04-gui-button.md](/components/ryll/plans/PLAN-bug-reports-phase-04-gui-button/) | Complete |
| 5. GUI: display region selection | [PLAN-bug-reports-phase-05-region-select.md](/components/ryll/plans/PLAN-bug-reports-phase-05-region-select/) | Complete |
| 6. GUI: live traffic viewer | [PLAN-bug-reports-phase-06-traffic-viewer.md](/components/ryll/plans/PLAN-bug-reports-phase-06-traffic-viewer/) | Complete |
| 7. Documentation and testing | [PLAN-bug-reports-phase-07-docs.md](/components/ryll/plans/PLAN-bug-reports-phase-07-docs/) | Complete |

### Phase 1: Ring buffer infrastructure

Add a per-channel ring buffer that retains the last N
seconds (or N bytes) of pcap-formatted protocol traffic.

- Add a `TrafficRingBuffer` struct to `capture.rs` (or a
  new `bugreport.rs` module) that wraps a
  `VecDeque<TrafficEntry>` with a byte-count cap.
- Each `TrafficEntry` contains:
  - `timestamp: Duration` — relative to session start.
  - `channel: String` — "main", "display", etc.
  - `direction: Direction` — Sent or Received.
  - `message_type: u16` — SPICE message type ID.
  - `message_name: String` — human-readable name (from
    `protocol::logging::message_names`).
  - `payload_size: u32` — message payload size.
  - `pcap_frame: Vec<u8>` — the full pcap frame (for
    writing to the bug report pcap file).
- `push()` appends an entry and evicts oldest entries when
  the byte cap is exceeded.
- `drain_to_pcap()` writes all buffered pcap frames to a
  file (for inclusion in the bug report zip).
- `recent()` returns a slice/iterator of recent entries
  for display in the traffic viewer UI (Phase 7).
- The ring buffer is always active (not gated behind
  `--capture`). It lives alongside the `CaptureSession`
  but is independent of it. Fixed 50 MB cap.
- Each channel handler calls `ring_buffer.push()` in its
  read loop and send method, mirroring the existing
  `capture.packet_sent()`/`capture.packet_received()` calls.

### Phase 2: Channel state snapshots

Add a `snapshot()` method to each channel handler that
serialises the channel's current mutable state to a
JSON-serialisable struct.

**DisplayChannel snapshot:**
- GLZ dictionary: entry count, list of cached image IDs,
  total memory usage in bytes.
- Last 20 image decode results: image ID, image type
  (LZ/GLZ/LZ4/Pixmap/JPEG/FromCache), dimensions, whether
  decompression succeeded.
- ACK state: generation, window, message count, last ACK.
- Buffer fill level.

**InputsChannel snapshot:**
- `button_state` bitmask (which buttons are pressed).
- `motion_count` (pending motion messages).
- Last 20 input events: event type (KeyDown/KeyUp/
  MouseDown/MouseUp/MouseMove), scancode or position,
  timestamp relative to session start.

**CursorChannel snapshot:**
- Cursor cache: entry count, list of cached cursor IDs
  with dimensions.
- Current cursor: position, visibility, shape metadata
  (width, height, hot spot).
- ACK state.

**MainChannel snapshot:**
- Session ID.
- Mouse mode.
- Bytes in/out.

**App-level snapshot:**
- FPS (current sliding window value).
- Bandwidth (current and recent history).
- Last latency measurement.
- Surface list: ID, dimensions, dirty flag.
- Uptime.

Implementation: each snapshot struct derives `Serialize`
from serde, or is manually written to JSON (matching the
pattern in `capture.rs` metadata). The snapshot is taken
on the main thread by sending a request through the event
channel and collecting responses, or by storing the
snapshot-relevant state in `Arc<Mutex<>>` so it can be
read from the UI thread.

**Design consideration**: channel handlers run as async
tasks on a separate thread from the egui UI. The simplest
approach is to add `Arc<Mutex<ChannelSnapshot>>` fields
that are updated in-place by each channel handler, and
read by the UI thread when assembling a bug report. This
avoids cross-thread request/response coordination.

### Phase 3: Bug report assembly and zip output

Add a `BugReport` struct that collects all the pieces and
writes a zip file.

- Add `zip` crate dependency (optional, gated behind
  `capture` feature or a new `bugreport` feature).
- `BugReport::new(report_type, channel, description)`
  creates the report.
- Collects: metadata JSON, channel state snapshot JSON,
  pcap from ring buffer, session statistics JSON.
- For display reports: full-surface PNG screenshot, region
  coordinates in metadata.
- Writes a zip file with structure:
  ```
  ryll-bugreport-2026-04-03T12-34-56Z.zip
  ├── metadata.json      # report type, description,
  │                       #   ryll version, platform,
  │                       #   target, timestamp
  ├── session.json        # FPS, bandwidth, latency,
  │                       #   uptime, surface list
  ├── channel-state.json  # snapshot of affected channel
  ├── traffic.pcap        # last ~30s of channel traffic
  └── screenshot.png      # (display reports only)
  ```
- PNG encoding: use the `png` crate (already an indirect
  dependency via `image`). Encode the surface RGBA pixels.
- File naming: `ryll-bugreport-YYYY-MM-DDTHH-MM-SSZ.zip`.
- Output location: capture dir if `--capture` is active,
  otherwise current working directory.

### Phase 4: GUI — report button and description dialog

Add a "Bug Report" button to the status bar and a modal
dialog flow for non-display reports.

- Add a small button (e.g. "Report") at the right end of
  the status bar, before the bandwidth sparkline.
- Clicking the button opens a modal panel with:
  1. A privacy warning: "Bug reports may contain sensitive
     data including screen contents, typed keystrokes, and
     protocol traffic. Review the report before sharing and
     ensure no confidential information is visible on screen
     or was recently typed."
  2. Channel selector (radio buttons: Display, Input,
     Cursor, Connection). Only show channels that are
     currently connected.
  3. Text input for a brief description (optional).
  4. "Capture" and "Cancel" buttons.
- For Display: clicking "Capture" enters region selection
  mode (Phase 5). For other channels: clicking "Capture"
  immediately assembles and writes the bug report.
- Show a brief status message ("Bug report saved to ...")
  in the status bar for a few seconds after writing.
- Keyboard shortcut: F12 opens the bug report dialog.

### Phase 5: GUI — display region selection

Add an interactive rectangle selection overlay for display
bug reports.

- After the user selects "Display" and clicks "Capture",
  the app enters a selection mode:
  - A translucent instruction banner is shown at the top
    of the surface: "Click and drag to select the affected
    region — press Escape to skip".
  - The cursor changes to a crosshair.
  - The user drags a rectangle over the corrupted region.
  - While dragging, a translucent red rectangle is drawn
    as an overlay (using egui's foreground painter, same
    layer as the cursor overlay).
  - On mouse release, the selected region coordinates
    are recorded.
  - The bug report is assembled with the region
    coordinates in the metadata and the full-surface PNG.
- Allow the user to skip region selection by pressing
  Escape or clicking "Skip" — this captures the full
  surface without a highlighted region.
- The region is purely metadata — the PNG always contains
  the full surface. But it could also be useful to draw
  the selection rectangle onto a *second* annotated PNG
  to make it visually obvious to the developer.

### Phase 6: GUI — live traffic viewer

Add a "Traffic" button to the status bar that opens a
scrollable panel showing recent protocol messages from the
ring buffer.

- A small "Traffic" button in the status bar (next to the
  "Report" button).
- Clicking it toggles a side panel or bottom panel showing
  a scrollable table of recent messages.
- Each row shows: timestamp (relative, e.g. "-2.3s"),
  channel name (colour-coded), direction arrow (→ sent,
  ← received), message type name, and payload size.
- The list auto-scrolls to show newest messages, with a
  pause button to freeze scrolling for inspection.
- Clicking a row could expand it to show hex dump of the
  first N bytes of the payload (optional, may defer).
- Channel filter checkboxes at the top of the panel to
  show/hide individual channels (e.g. hide the noisy
  display channel to focus on inputs).
- The panel reads from the ring buffer's `recent()`
  iterator via the shared `Arc<Mutex<>>` state. Since
  egui repaints at ~60fps, this gives near-real-time
  visibility.
- Keyboard shortcut: F11 toggles the traffic viewer.

### Phase 7: Documentation and testing

- Update `README.md` to document the bug report feature,
  including the keyboard shortcut (F12) and what's
  captured in each report type.
- Update `ARCHITECTURE.md` with the ring buffer and
  snapshot architecture.
- Update `AGENTS.md` with new modules and dependencies.
- Add a section to `docs/troubleshooting.md` about using
  bug reports.
- Add unit tests for:
  - `TrafficRingBuffer` push/eviction/drain.
  - Channel snapshot serialisation.
  - Zip file assembly (write and verify contents).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Pressing F12 (or clicking "Report" in the status bar)
  opens a bug report dialog.
* Selecting "Display" and dragging a region produces a zip
  file containing metadata.json, session.json,
  channel-state.json (with GLZ dictionary summary),
  traffic.pcap (last ~30s of display traffic), and
  screenshot.png (full surface).
* Selecting "Input" produces a zip with input channel state
  (modifier keys, button state, recent events) and input
  traffic pcap.
* The zip files are self-contained and can be shared as
  email attachments or GitHub issue attachments.
* The ring buffer respects the configured memory limit and
  evicts old data gracefully.
* The feature works without `--capture` (ring buffer is
  always active).
* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have been
  updated.

### Dependencies

- `zip` crate for zip file output (pure Rust, MIT).
- `png` crate for screenshot encoding (already an indirect
  dependency via the `image` crate; may need to add as a
  direct dependency).
- `serde` + `serde_json` for channel state serialisation
  (already an indirect dependency via `mp4`; would need to
  be added as direct dependencies).

### Future work

- **Replay mode**: load a bug report zip and replay the
  pcap traffic to reproduce the issue without a live server.
- **Auto-detect corruption**: compare GLZ-decompressed
  output against expected checksums; auto-trigger a bug
  report when a mismatch is detected.
- **USB redirect reports**: when usbredir channels are
  implemented, add a report type that captures device
  enumeration, claim state, and recent USB traffic.
- **Remote submission**: option to upload the zip to a
  configured endpoint (e.g. GitHub issue API or a
  simple HTTP POST).
- **Annotated screenshot**: draw the selection rectangle
  and a text label onto a second copy of the PNG for
  visual clarity.
- **Multiple region selection**: allow highlighting several
  disconnected regions of corruption in a single report.

### Bugs fixed during this work

(none yet)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
