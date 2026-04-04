# Phase 1: Ring buffer infrastructure

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

## Goal

Add an always-active per-channel ring buffer that retains
the most recent protocol traffic (up to a 50 MB total cap)
in a format suitable for both:

1. Dumping to a pcap file in a bug report (Phase 3).
2. Displaying live in a traffic viewer panel (Phase 6).

At the end of this phase, the ring buffer is populated by
all four channel handlers and can be read from the UI
thread, but no UI or bug report consumer exists yet.

## Design

### Data structures

A new module `src/bugreport.rs` will contain:

```rust
/// Direction of a protocol message.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrafficDirection {
    Sent,
    Received,
}

/// A single protocol message recorded in the ring buffer.
///
/// Stores both structured metadata (for the live traffic
/// viewer) and the raw pcap frame bytes (for bug report
/// export). The pcap frame is the same format that
/// `PcapChannelWriter` produces: Ethernet + IPv4 + TCP
/// headers wrapping the raw SPICE payload.
#[derive(Debug, Clone)]
pub struct TrafficEntry {
    /// Time elapsed since session start.
    pub timestamp: std::time::Duration,
    /// Which channel this message belongs to.
    pub channel: &'static str,
    /// Sent by the client or received from the server.
    pub direction: TrafficDirection,
    /// SPICE message type ID (from the 6-byte mini-header).
    pub message_type: u16,
    /// Human-readable message name (e.g. "draw_copy").
    pub message_name: &'static str,
    /// Total wire size including the 6-byte header.
    pub wire_size: u32,
    /// Payload size (wire_size minus 6-byte header).
    pub payload_size: u32,
    /// Full pcap frame bytes (Ethernet + IP + TCP + SPICE
    /// payload).  Used by `drain_to_pcap()` for bug report
    /// export.
    pub pcap_frame: Vec<u8>,
}
```

```rust
/// Per-channel ring buffer of recent protocol traffic.
///
/// Shared between a channel handler (which pushes entries)
/// and the UI thread (which reads for the traffic viewer
/// and bug reports).  Wrapped in `Arc<Mutex<>>`.
pub struct TrafficRingBuffer {
    /// Ring of entries, newest at the back.
    entries: VecDeque<TrafficEntry>,
    /// Current total byte count of all pcap_frame data.
    total_bytes: usize,
    /// Maximum byte count before eviction.
    max_bytes: usize,
}
```

The 50 MB cap applies to the sum of `pcap_frame.len()`
across all entries across all channels. Each channel gets
its own `TrafficRingBuffer` instance (not a single shared
buffer) so that locking is per-channel. The 50 MB is
divided equally: 12.5 MB per channel. This prevents a
noisy display channel from evicting all traffic from the
quiet inputs channel.

### TrafficRingBuffer methods

```rust
impl TrafficRingBuffer {
    /// Create a new ring buffer with the given byte cap.
    pub fn new(max_bytes: usize) -> Self;

    /// Push a new entry, evicting oldest entries if the
    /// byte cap would be exceeded.
    pub fn push(&mut self, entry: TrafficEntry);

    /// Return a slice of all buffered entries (oldest
    /// first) for the traffic viewer UI.
    pub fn entries(&self) -> &VecDeque<TrafficEntry>;

    /// Write all buffered pcap frames to a pcap file at
    /// the given path.  Returns the number of frames
    /// written.
    pub fn drain_to_pcap(&self, path: &std::path::Path)
        -> anyhow::Result<usize>;

    /// Current number of entries.
    pub fn len(&self) -> usize;

    /// Current byte usage.
    pub fn total_bytes(&self) -> usize;
}
```

### Sharing with the UI thread

Each channel handler already receives an
`Option<Arc<CaptureSession>>`. The ring buffers need a
similar pattern but are always active (not gated behind
`--capture`).

Introduce a `TrafficBuffers` struct that holds all four
per-channel ring buffers:

```rust
pub struct TrafficBuffers {
    pub main: Mutex<TrafficRingBuffer>,
    pub display: Mutex<TrafficRingBuffer>,
    pub inputs: Mutex<TrafficRingBuffer>,
    pub cursor: Mutex<TrafficRingBuffer>,
}
```

An `Arc<TrafficBuffers>` is created in `main.rs` (or
`RyllApp::new()`) and cloned into each channel constructor
— the same pattern as `Arc<ByteCounter>` and
`Option<Arc<CaptureSession>>`.

### Pcap frame construction

The ring buffer entries need pcap frames for bug report
export. The existing `build_tcp_frame()` function in
`capture.rs` constructs these frames but is currently
private to that module and only called from
`PcapChannelWriter`.

Refactor: make `build_tcp_frame()` and `channel_port()`
`pub(crate)` so they can be called from the channel
handlers (or from `bugreport.rs`). Alternatively, add a
helper on `TrafficRingBuffer` or `TrafficBuffers` that
constructs the frame and pushes the entry in one call:

```rust
impl TrafficBuffers {
    /// Record a received message.  Constructs the pcap
    /// frame and pushes to the appropriate ring buffer.
    pub fn record_received(
        &self,
        channel: &'static str,
        elapsed: Duration,
        msg_type: u16,
        msg_name: &'static str,
        raw_message: &[u8],  // full message incl header
    );

    /// Record a sent message.
    pub fn record_sent(
        &self,
        channel: &'static str,
        elapsed: Duration,
        msg_type: u16,
        msg_name: &'static str,
        raw_message: &[u8],
    );
}
```

These methods internally call `build_tcp_frame()` with the
appropriate source/dest IPs and ports, matching the
existing pcap capture conventions (client=10.0.0.1,
server=10.0.0.2, port from `channel_port()`).

**TCP sequence numbers**: the ring buffer pcap frames need
consistent TCP sequence numbers for Wireshark reassembly.
Each `TrafficBuffers` channel tracks its own client\_seq
and server\_seq counters (same pattern as
`PcapChannelWriter`).

### Integration points

The ring buffer needs to be populated with structured
entries — not just raw bytes, but also the message type
and name. This means we cannot record at the same point
as the existing `capture.packet_received()` calls (which
fire before message headers are parsed). Instead:

**For received messages** — record inside
`process_messages()`, after the `MessageHeader` has been
parsed and the full message has been extracted from the
buffer:

```
// Existing code (all four channels):
let header = MessageHeader::read(&self.buffer)?;
let total_size = MessageHeader::SIZE + header.message_size as usize;
let payload = self.buffer[MessageHeader::SIZE..total_size].to_vec();
self.buffer.drain(..total_size);

// NEW: record to ring buffer
let raw_message = /* reconstruct from header + payload, or
   capture before drain */;
self.traffic.record_received(
    "display",
    self.session_start.elapsed(),
    header.message_type,
    message_names::display_server(header.message_type),
    &raw_message,
);
```

To avoid reconstructing the message bytes, capture the
slice `&self.buffer[..total_size]` before the `drain()`
call.

**For sent messages** — record inside `send_with_log()`,
which already has the `msg_type: u16` parameter:

```
// Existing code (all four channels):
async fn send_with_log(&mut self, msg_type: u16, data: &[u8]) {
    // NEW: record to ring buffer
    self.traffic.record_sent(
        "display",
        self.session_start.elapsed(),
        msg_type,
        message_names::display_client(msg_type),
        data,
    );

    self.send(data).await
}
```

### Session start timestamp

The ring buffer entries need a `Duration` relative to
session start. Currently `CaptureSession` has a `start:
Instant` field, but the ring buffer is independent of
capture.

Add a `session_start: Instant` field to each channel
handler struct, set in the constructor. This is the same
`Instant::now()` from when the connection was established.
Pass it in from `run_connection()` in `app.rs`.

Alternatively, store `Instant::now()` in
`TrafficBuffers` at creation time and let channels
reference it. This is simpler and ensures all channels
use the same epoch.

**Decision**: store `start: Instant` in `TrafficBuffers`.
Add a `TrafficBuffers::elapsed(&self) -> Duration` helper.

## Steps

### Step 1: Create `src/bugreport.rs` with data structures

1. Create `src/bugreport.rs` with:
   - `TrafficDirection` enum.
   - `TrafficEntry` struct.
   - `TrafficRingBuffer` struct with `new()`, `push()`,
     `entries()`, `drain_to_pcap()`, `len()`,
     `total_bytes()`.
   - `TrafficBuffers` struct with per-channel
     `Mutex<TrafficRingBuffer>` fields, `start: Instant`,
     `elapsed()`, `record_sent()`, `record_received()`.
2. Add `mod bugreport;` to `main.rs`.
3. Make `build_tcp_frame()` and `channel_port()` in
   `capture.rs` `pub(crate)`.
4. `drain_to_pcap()` uses `pcap-file`'s `PcapWriter` (same
   as existing capture code) to write buffered frames.

### Step 2: Wire TrafficBuffers into channel constructors

1. Create `Arc<TrafficBuffers>` in `run_connection()` in
   `app.rs` (alongside the existing `byte_counter`).
2. Also store `Arc<TrafficBuffers>` in `RyllApp` so the UI
   thread can read it later (for Phase 6 traffic viewer and
   Phase 3 bug reports).
3. Clone and pass to each channel constructor:
   `MainChannel::new(..., traffic.clone())`,
   `DisplayChannel::new(..., traffic.clone())`, etc.
4. Add `traffic: Arc<TrafficBuffers>` field to each channel
   handler struct.
5. In headless mode (`run_headless()`), create and pass
   `Arc<TrafficBuffers>` the same way.

### Step 3: Record received messages

For each of the four channel handlers, modify
`process_messages()` to record to the ring buffer:

1. Before `self.buffer.drain(..total_size)`, capture the
   raw message bytes: `let raw = self.buffer[..total_size].to_vec();`
2. After extracting the header, call
   `self.traffic.record_received(...)` with the channel
   name, message type, message name (from the appropriate
   `message_names::*_server()` function), and raw bytes.
3. The four files to modify:
   - `src/channels/display.rs` — `process_messages()`
     around line 250.
   - `src/channels/inputs.rs` — `process_messages()`
     around line 161.
   - `src/channels/cursor.rs` — `process_messages()`
     around line 100.
   - `src/channels/main_channel.rs` — `process_messages()`
     around line 95.

### Step 4: Record sent messages

For each of the four channel handlers, modify
`send_with_log()` to record to the ring buffer:

1. Call `self.traffic.record_sent(...)` with the channel
   name, `msg_type`, message name (from the appropriate
   `message_names::*_client()` function), and `data`.
2. The four files to modify:
   - `src/channels/display.rs` — `send_with_log()` at
     line 761.
   - `src/channels/inputs.rs` — `send_with_log()` at
     line 347.
   - `src/channels/cursor.rs` — `send_with_log()` at
     line 370.
   - `src/channels/main_channel.rs` — `send_with_log()`
     at line 281.

**Note**: the inputs channel has some sends that bypass
`send_with_log()` and call `send()` directly (e.g.
`handle_input_event()` for key/mouse events at lines
278-334). These should be changed to go through
`send_with_log()`, or the ring buffer recording should
also be added to the `send()` method. Since `send()` does
not have the `msg_type` parameter, the cleanest approach
is to route all sends through `send_with_log()`.

Review all four channels to confirm no sends bypass
`send_with_log()`. Fix any that do.

### Step 5: Build and test

1. `pre-commit run --all-files` must pass.
2. `make build` must succeed.
3. Manual verification: run ryll with `--verbose` against
   a test SPICE server and confirm log output shows ring
   buffer activity (add a debug log in `push()` showing
   entry count and byte usage, gated behind verbose mode).
4. Verify ring buffer eviction by checking that
   `total_bytes()` stays below the 12.5 MB per-channel
   cap during a session with moderate display traffic.

### Step 6: Update documentation

1. Update `ARCHITECTURE.md` to describe the ring buffer
   and its role in bug reports and the traffic viewer.
2. Update `AGENTS.md` to list `src/bugreport.rs` in the
   code organisation section.
3. Update `README.md` to mention that recent traffic is
   always buffered for bug reports.

## Administration and logistics

### Success criteria

* `Arc<TrafficBuffers>` is created at startup and shared
  with all four channel handlers and the app struct.
* Every received and sent SPICE message is recorded in the
  per-channel ring buffer with correct metadata (channel,
  direction, message type, message name, wire size).
* The ring buffer respects the 12.5 MB per-channel byte
  cap and evicts oldest entries when exceeded.
* `drain_to_pcap()` produces a valid pcap file that
  Wireshark can open.
* `entries()` returns entries in chronological order.
* `pre-commit run --all-files` passes.
* The ring buffer is active regardless of whether
  `--capture` is specified.
* `make build` succeeds on the first attempt.

### Risks

* **Performance**: constructing pcap frames for every
  message adds CPU overhead. Mitigated by the fact that
  `build_tcp_frame()` is already called for every message
  in capture mode with no reported performance issues.
  The ring buffer adds a `VecDeque::push_back()` and
  potential `pop_front()` per message, which is O(1).

* **Locking**: the `Mutex<TrafficRingBuffer>` is held
  briefly per message (one push). The UI thread reads via
  `entries()` at ~60fps. Contention should be negligible
  since pushes and reads are both fast. If it becomes an
  issue, we can switch to a lock-free ring buffer later,
  but this is premature optimisation for now.

* **Memory**: 50 MB total (4 x 12.5 MB) is the cap. Actual
  usage will be lower for quiet sessions. Display-heavy
  sessions will hit the cap and evict, which is correct
  behaviour.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
