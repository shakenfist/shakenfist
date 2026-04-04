# Phase 2: Channel state snapshots

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

## Goal

Add per-channel snapshot structs that capture the mutable
state of each channel handler at the moment a bug report is
triggered. Each snapshot is a plain Rust struct that derives
`Serialize` (via serde) so it can be written to JSON in the
bug report zip (Phase 3).

At the end of this phase:

1. Each channel handler maintains an `Arc<Mutex<T>>` of its
   snapshot struct, updated in-place as state changes.
2. The app holds an `Arc<Mutex<AppSnapshot>>` for app-level
   state (FPS, bandwidth, surfaces, uptime).
3. The snapshot structs are readable from the UI thread
   without blocking the channel handler (the mutex is held
   only briefly during update).
4. `serde` and `serde_json` are direct dependencies in
   `Cargo.toml`.
5. No UI or bug report consumer exists yet — that is
   Phase 3+.

## Design

### Dependency: serde and serde_json

Neither `serde` nor `serde_json` is a direct dependency
today. Add them to `[dependencies]` in `Cargo.toml`:

```toml
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

These are unconditional dependencies (not gated behind a
feature flag) because the snapshot structs are always
compiled — they're lightweight and serde is already an
indirect dependency via `mp4`.

### Snapshot structs

All snapshot structs live in `src/bugreport.rs` alongside
the existing `TrafficEntry` / `TrafficRingBuffer` /
`TrafficBuffers` types. Each derives `Serialize` so
Phase 3 can call `serde_json::to_string_pretty()`.

#### DisplaySnapshot

```rust
#[derive(Debug, Clone, Serialize)]
pub struct DisplaySnapshot {
    /// Number of entries in the GLZ image cache.
    pub image_cache_entries: usize,
    /// List of image IDs currently in the cache.
    pub image_cache_ids: Vec<u64>,
    /// Total bytes of decoded RGBA pixel data in cache.
    pub image_cache_bytes: usize,
    /// Last N image decode results.
    pub recent_decodes: VecDeque<DecodeResult>,
    /// ACK generation ID from the server's SetAck message.
    pub ack_generation: u32,
    /// Messages between ACKs.
    pub ack_window: u32,
    /// Total messages received on this channel.
    pub message_count: u32,
    /// message_count when the last ACK was sent.
    pub last_ack: u32,
    /// Total bytes received on the display channel.
    pub bytes_in: u64,
    /// Total bytes sent on the display channel.
    pub bytes_out: u64,
}
```

```rust
#[derive(Debug, Clone, Serialize)]
pub struct DecodeResult {
    /// SPICE image type (e.g. "GlzRgb", "Lz4", "Pixmap").
    pub image_type: String,
    /// Image ID from the ImageDescriptor.
    pub image_id: u64,
    /// Decoded width in pixels.
    pub width: u32,
    /// Decoded height in pixels.
    pub height: u32,
    /// Whether this was a cache hit (FromCache type).
    pub from_cache: bool,
    /// Whether decompression succeeded.
    pub success: bool,
    /// Seconds since session start when this decode occurred.
    pub timestamp_secs: f64,
}
```

The `recent_decodes` deque is capped at 20 entries (oldest
evicted on push). This captures enough history to show what
was happening around the time of the bug report without
unbounded growth.

#### InputsSnapshot

```rust
#[derive(Debug, Clone, Serialize)]
pub struct InputsSnapshot {
    /// Bitmask of currently held mouse buttons.
    pub button_state: u32,
    /// Number of unacknowledged mouse position messages.
    pub motion_count: u32,
    /// Seconds since last key press, or None.
    pub secs_since_last_key: Option<f64>,
    /// Last N input events.
    pub recent_events: VecDeque<InputEventRecord>,
    /// Total bytes received on the inputs channel.
    pub bytes_in: u64,
    /// Total bytes sent on the inputs channel.
    pub bytes_out: u64,
}
```

```rust
#[derive(Debug, Clone, Serialize)]
pub struct InputEventRecord {
    /// "KeyDown", "KeyUp", "MouseDown", "MouseUp",
    /// "MouseMove".
    pub event_type: String,
    /// Scancode for key events, 0 for mouse events.
    pub scancode: u32,
    /// Mouse position for mouse events (0,0 for keys).
    pub x: u32,
    pub y: u32,
    /// Button bitmask for mouse press/release events.
    pub button_mask: u32,
    /// Seconds since session start.
    pub timestamp_secs: f64,
}
```

The `recent_events` deque is capped at 50 entries. Input
events are smaller and less frequent than display decodes,
so a larger window is useful for reproducing input bugs.

Note: the inputs channel does not currently store recent
events — only `last_key_time`, `button_state`, and
`motion_count`. The `InputEventRecord` tracking is new
state that must be added to the channel handler alongside
the snapshot.

#### CursorSnapshot

```rust
#[derive(Debug, Clone, Serialize)]
pub struct CursorSnapshot {
    /// Number of entries in the cursor shape cache.
    pub cache_entries: usize,
    /// Summary of each cached cursor.
    pub cache_contents: Vec<CursorCacheEntry>,
    /// ACK generation ID.
    pub ack_generation: u32,
    /// Messages between ACKs.
    pub ack_window: u32,
    /// Total messages received.
    pub message_count: u32,
    /// message_count at last ACK.
    pub last_ack: u32,
    /// Total bytes received on the cursor channel.
    pub bytes_in: u64,
    /// Total bytes sent on the cursor channel.
    pub bytes_out: u64,
}
```

```rust
#[derive(Debug, Clone, Serialize)]
pub struct CursorCacheEntry {
    /// Unique ID from the SpiceCursorHeader.
    pub cursor_id: u64,
    /// Width in pixels.
    pub width: u16,
    /// Height in pixels.
    pub height: u16,
    /// Hot-spot X offset.
    pub hot_spot_x: u16,
    /// Hot-spot Y offset.
    pub hot_spot_y: u16,
}
```

Cursor position and visibility are app-level state (held
in `RyllApp`), not channel-level, so they appear in
`AppSnapshot` instead.

#### MainSnapshot

```rust
#[derive(Debug, Clone, Serialize)]
pub struct MainSnapshot {
    /// Session ID from the server's Init message.
    pub session_id: Option<u32>,
    /// Total bytes received on the main channel.
    pub bytes_in: u64,
    /// Total bytes sent on the main channel.
    pub bytes_out: u64,
}
```

The main channel holds minimal mutable state. Mouse mode
and channel list are delivered via `ChannelEvent` and
tracked by the app, so they appear in `AppSnapshot`.

#### AppSnapshot

```rust
#[derive(Debug, Clone, Serialize)]
pub struct AppSnapshot {
    /// Current FPS from the sliding-window calculation.
    pub fps: f64,
    /// Recent bandwidth samples in bytes/sec (last 60s).
    pub bandwidth_history: Vec<f32>,
    /// Most recent bandwidth sample in bytes/sec.
    pub bandwidth_current: f32,
    /// Last measured key-to-display latency in seconds.
    pub last_latency: Option<f64>,
    /// Total frames received (DisplayMark events).
    pub frames_received: u64,
    /// List of active surfaces.
    pub surfaces: Vec<SurfaceInfo>,
    /// Cursor screen position.
    pub cursor_pos: (u16, u16),
    /// Whether the cursor is currently visible.
    pub cursor_visible: bool,
    /// Current mouse mode (1=server, 2=client).
    pub mouse_mode: u32,
    /// Whether the session is connected.
    pub connected: bool,
    /// Session uptime in seconds.
    pub uptime_secs: f64,
}
```

```rust
#[derive(Debug, Clone, Serialize)]
pub struct SurfaceInfo {
    pub surface_id: u32,
    pub width: u32,
    pub height: u32,
}
```

### Sharing pattern: Arc<Mutex<T>>

The master plan specifies `Arc<Mutex<ChannelSnapshot>>` for
cross-thread sharing. The concrete pattern:

1. Each channel handler struct gains a field:
   ```rust
   snapshot: Arc<Mutex<DisplaySnapshot>>,  // etc.
   ```

2. The snapshot is created with sensible defaults in the
   channel constructor and the `Arc` is cloned into the
   app (via the same `run_connection()` path used for
   `Arc<TrafficBuffers>`).

3. The channel handler updates the snapshot in-place at
   two points:
   - **After each received message is processed** — update
     ACK state, bytes_in, and any message-specific state
     (e.g. push to `recent_decodes` for display).
   - **After each sent message** — update bytes_out.

4. Updates lock the mutex, clone or update fields, and
   release. The lock is held for microseconds (copying a
   handful of scalars and a small VecDeque). No allocations
   inside the lock except when pushing to a capped deque.

5. The app reads snapshots when assembling a bug report
   (Phase 3) by locking each mutex and cloning the struct.

### Snapshot collection struct

A convenience struct to hold all the snapshot `Arc`s in one
place, similar to `TrafficBuffers`:

```rust
pub struct ChannelSnapshots {
    pub display: Arc<Mutex<DisplaySnapshot>>,
    pub inputs: Arc<Mutex<InputsSnapshot>>,
    pub cursor: Arc<Mutex<CursorSnapshot>>,
    pub main: Arc<Mutex<MainSnapshot>>,
}
```

This is created in `run_connection()` alongside
`TrafficBuffers`, stored in `RyllApp`, and each channel
receives its own `Arc<Mutex<T>>` clone.

The `AppSnapshot` is separate — it lives in `RyllApp` as
an `Arc<Mutex<AppSnapshot>>` and is updated by the app's
own event-processing loop (FPS, bandwidth, surface changes,
cursor state).

### Update frequency

Not every field needs updating on every message. The
strategy per channel:

**DisplayChannel** — update on every `process_messages()`
call:
- `ack_generation`, `ack_window`, `message_count`,
  `last_ack` — after each message.
- `image_cache_entries`, `image_cache_ids`,
  `image_cache_bytes` — after `handle_draw_copy()` when
  the cache is modified (image inserted or evicted).
- `recent_decodes` — push after each `handle_draw_copy()`.
- `bytes_in`, `bytes_out` — after each recv/send.

**InputsChannel** — update on every input event:
- `button_state`, `motion_count` — after each input event.
- `recent_events` — push after each send_with_log().
- `bytes_in`, `bytes_out` — after each recv/send.

**CursorChannel** — update after each message:
- `cache_contents` — after cache insert/invalidate.
- ACK state and bytes — after each message.

**MainChannel** — update on init and per-message:
- `session_id` — once on init.
- `bytes_in`, `bytes_out` — after each recv/send.

**RyllApp** — update from the event loop:
- `fps` — on each `DisplayMark` event.
- `bandwidth_*` — on each bandwidth tick (once per second).
- `surfaces` — on `SurfaceCreated` / `SurfaceDestroyed`.
- `cursor_*`, `mouse_mode`, `connected` — on the relevant
  `ChannelEvent`.
- `uptime_secs` — computed at snapshot read time from
  `TrafficBuffers::elapsed()`.

## Steps

### Step 1: Add serde dependencies

Add `serde` and `serde_json` to `Cargo.toml`
`[dependencies]`:

```toml
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### Step 2: Define snapshot structs in bugreport.rs

Add to `src/bugreport.rs`:

1. `use serde::Serialize;`
2. `DecodeResult` struct with `#[derive(Serialize)]`.
3. `DisplaySnapshot` struct with `#[derive(Serialize)]`
   and a `Default` impl (empty cache, zero counters).
4. `InputEventRecord` struct.
5. `InputsSnapshot` struct with `Default` impl.
6. `CursorCacheEntry` struct.
7. `CursorSnapshot` struct with `Default` impl.
8. `MainSnapshot` struct with `Default` impl.
9. `SurfaceInfo` struct.
10. `AppSnapshot` struct with `Default` impl.
11. `ChannelSnapshots` struct holding the four channel
    `Arc<Mutex<T>>` values, with a `new()` constructor.

### Step 3: Wire ChannelSnapshots into channel constructors

1. Create `ChannelSnapshots::new()` in `run_connection()`
   in `app.rs`, alongside the existing `TrafficBuffers`.
2. Store the `ChannelSnapshots` in `RyllApp` (add field).
3. Pass each channel's `Arc<Mutex<T>>` to its constructor:
   - `DisplayChannel::new(..., snapshots.display.clone())`
   - `InputsChannel::new(..., snapshots.inputs.clone())`
   - `CursorChannel::new(..., snapshots.cursor.clone())`
   - `MainChannel::new(..., snapshots.main.clone())`
4. Add a `snapshot: Arc<Mutex<T>>` field to each channel
   handler struct.
5. Create and store `Arc<Mutex<AppSnapshot>>` in `RyllApp`.
6. Do the same in `run_headless()`.

### Step 4: Update DisplayChannel to maintain its snapshot

1. Add `snapshot: Arc<Mutex<DisplaySnapshot>>` field.
2. After `handle_draw_copy()` completes (success or
   failure), lock the snapshot and:
   - Push a `DecodeResult` to `recent_decodes` (cap at 20,
     pop_front if full).
   - Update `image_cache_entries`, `image_cache_ids` (sorted
     list of keys from `previous_images`), and
     `image_cache_bytes` (sum of `v.len()` for all values).
3. After processing each message in `process_messages()`,
   update `ack_generation`, `ack_window`, `message_count`,
   `last_ack`, `bytes_in`.
4. In `send_with_log()`, update `bytes_out`.
5. The snapshot update should be a small helper method:
   ```rust
   fn update_snapshot(&self) {
       let mut snap = self.snapshot.lock().unwrap();
       snap.ack_generation = self.ack_generation;
       snap.ack_window = self.ack_window;
       snap.message_count = self.message_count;
       snap.last_ack = self.last_ack;
       snap.bytes_in = self.bytes_in;
       snap.bytes_out = self.bytes_out;
       snap.image_cache_entries =
           self.previous_images.len();
       snap.image_cache_bytes =
           self.previous_images.values()
               .map(|v| v.len()).sum();
       snap.image_cache_ids = {
           let mut ids: Vec<u64> =
               self.previous_images.keys().copied()
                   .collect();
           ids.sort_unstable();
           ids
       };
   }
   ```
   Call this at the end of `process_messages()` (once per
   batch, not per individual message) and in
   `send_with_log()`.

### Step 5: Update InputsChannel to maintain its snapshot

1. Add `snapshot: Arc<Mutex<InputsSnapshot>>` field.
2. Add a `recent_events: VecDeque<InputEventRecord>` field
   to the channel struct itself (not just the snapshot) so
   events can be recorded without locking the snapshot on
   every keystroke. Cap at 50.
3. In `handle_input_event()` / `send_with_log()`, push an
   `InputEventRecord` to the local deque.
4. At the end of `send_with_log()`, lock the snapshot and
   sync all fields:
   ```rust
   fn update_snapshot(&self) {
       let mut snap = self.snapshot.lock().unwrap();
       snap.button_state = self.button_state;
       snap.motion_count = self.motion_count;
       snap.secs_since_last_key = self.last_key_time
           .map(|t| t.elapsed().as_secs_f64());
       snap.recent_events = self.recent_events.clone();
       snap.bytes_in = self.bytes_in;
       snap.bytes_out = self.bytes_out;
   }
   ```
5. Also call `update_snapshot()` at the end of
   `process_messages()`.

### Step 6: Update CursorChannel to maintain its snapshot

1. Add `snapshot: Arc<Mutex<CursorSnapshot>>` field.
2. After cache modifications (insert, invalidate_all,
   invalidate_one), rebuild `cache_contents`:
   ```rust
   fn update_snapshot(&self) {
       let mut snap = self.snapshot.lock().unwrap();
       snap.cache_entries = self.cursor_cache.len();
       snap.cache_contents = self.cursor_cache.iter()
           .map(|(&id, img)| CursorCacheEntry {
               cursor_id: id,
               width: img.width,
               height: img.height,
               hot_spot_x: img.hot_spot_x,
               hot_spot_y: img.hot_spot_y,
           })
           .collect();
       snap.ack_generation = self.ack_generation;
       snap.ack_window = self.ack_window;
       snap.message_count = self.message_count;
       snap.last_ack = self.last_ack;
       snap.bytes_in = self.bytes_in;
       snap.bytes_out = self.bytes_out;
   }
   ```
3. Call at end of `process_messages()` and `send_with_log()`.

### Step 7: Update MainChannel to maintain its snapshot

1. Add `snapshot: Arc<Mutex<MainSnapshot>>` field.
2. Update after processing each message:
   ```rust
   fn update_snapshot(&self) {
       let mut snap = self.snapshot.lock().unwrap();
       snap.session_id = self.session_id;
       snap.bytes_in = self.bytes_in;
       snap.bytes_out = self.bytes_out;
   }
   ```
3. Call at end of `process_messages()` and `send_with_log()`.

### Step 8: Update RyllApp to maintain AppSnapshot

1. Add `app_snapshot: Arc<Mutex<AppSnapshot>>` field.
2. Compute and update on each relevant `ChannelEvent`:
   - `DisplayMark` — update `fps`, `frames_received`.
   - `SurfaceCreated` / `SurfaceDestroyed` — rebuild
     `surfaces` list.
   - `CursorPosition` / `CursorVisibility` — update
     `cursor_pos`, `cursor_visible`.
   - `MouseMode` — update `mouse_mode`.
   - `SessionInitialized` — set `connected = true`.
   - `Disconnected` — set `connected = false`.
3. Update `bandwidth_current` and `bandwidth_history` in
   the bandwidth tick (where `BandwidthTracker::tick()` is
   called).
4. `uptime_secs` is computed at read time, not stored:
   when Phase 3 reads the snapshot, it calls
   `traffic.elapsed().as_secs_f64()` and sets the field
   before serialisation.

### Step 9: Build and validate

1. `pre-commit run --all-files` must pass.
2. `make build` must succeed.
3. Verify that the snapshot structs serialise correctly by
   adding a temporary `#[cfg(test)]` test that creates
   each snapshot with sample data, serialises to JSON with
   `serde_json::to_string_pretty()`, and asserts the output
   contains expected field names.

### Step 10: Update documentation

1. Update `ARCHITECTURE.md` to describe the snapshot structs
   and the `Arc<Mutex<T>>` sharing pattern.
2. Update `AGENTS.md` to note the serde dependency and the
   snapshot types in `bugreport.rs`.
3. Update `README.md` to mention that channel state is
   captured in bug reports.

## Administration and logistics

### Success criteria

* `serde` and `serde_json` are direct dependencies.
* Each of the four channel handlers maintains an
  `Arc<Mutex<T>>` snapshot that is updated on every
  message processed and every message sent.
* `RyllApp` maintains an `Arc<Mutex<AppSnapshot>>` updated
  from the event loop.
* All snapshot structs derive `Serialize` and produce
  valid, human-readable JSON via `serde_json`.
* The `ChannelSnapshots` and `AppSnapshot` `Arc`s are
  accessible from `RyllApp` for Phase 3 to read.
* `pre-commit run --all-files` passes.
* `make build` succeeds on the first attempt.
* No measurable performance regression — snapshot updates
  are O(1) per message except for the display cache
  summary (O(n) in cache size, but the cache is small).

### Risks

* **Lock contention**: The snapshot mutex is held briefly
  per message. The UI thread reads it only when a bug
  report is triggered (Phase 3), not every frame. In
  Phase 6 the traffic viewer reads the ring buffer at
  60fps, but that's a different mutex. Contention risk
  is negligible.

* **Serde binary size**: Adding serde derive increases the
  binary. This is acceptable — serde is already an indirect
  dependency and the derive macros add minimal code for
  small structs.

* **Clone cost of VecDeque in InputsSnapshot**: Cloning 50
  `InputEventRecord` entries is ~2 KB of data. The clone
  happens only when the snapshot is read (bug report
  trigger), not on every update. Acceptable.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
