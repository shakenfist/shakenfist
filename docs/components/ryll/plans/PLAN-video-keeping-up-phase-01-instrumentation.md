# Phase 1: Display-channel instrumentation

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
referenced source files, understand existing patterns (the
channel snapshot system, the `recent_decodes` ring, the
`channel-state.json` bug-report artefact), and ground your
answers in what the code actually does today. Do not speculate
about the codebase when you could read it instead.

## Goal

Add enough display-channel signal to a bug report's
`channel-state.json` that a maintainer can tell, without
re-running the session, whether a "video not keeping up"
complaint is caused by slow image decode, the read loop
falling behind the socket, or SPICE-level ACK backpressure.

At the end of this phase no threading changes have been made.
The display channel still runs as a single async task; we have
only added measurement of its hot path. The data this phase
produces is what justifies (or refutes) phases 2–4.

Concretely, the bug report's `channel-state.json` for the
`display` channel gains three families of fields:

1. **Per-decode wall time** on every entry in `recent_decodes`,
   plus session-cumulative counters and a min/max/mean over the
   recent ring.
2. **Socket-read fill stats**: how often the 256 KB read chunk
   came back full (a cheap proxy for "the OS recv buffer had
   bytes waiting when we read") and the max chunk size observed.
3. **ACK-send stats**: total ACKs sent and the recent interval
   distribution between ACK sends.

Scope is display only. Cursor / playback / main are out of
scope for this phase; if their data is needed later we revisit.

## Design

### Per-decode wall time

`DecodeResult` in
`shakenfist-spice-renderer/src/snapshots.rs:15-31` currently
records image type, dimensions, cache flag, success flag, and a
session-relative timestamp — but no duration. Add:

```rust
#[derive(Debug, Clone, Serialize)]
pub struct DecodeResult {
    pub image_type: String,
    pub image_id: u64,
    pub width: u32,
    pub height: u32,
    pub from_cache: bool,
    pub success: bool,
    pub timestamp_secs: f64,
    /// Wall-clock microseconds spent decompressing this image.
    /// Zero for `FromCache` and for failures that short-circuit
    /// before the decoder is invoked.
    pub decode_duration_us: u32,
}
```

`u32` microseconds covers ~71 minutes per decode, which is well
beyond any plausible per-image budget. Microseconds (not
milliseconds) keep small Lz4 / Pixmap decodes resolvable; many
finish in well under 1 ms.

In `shakenfist-spice-renderer/src/channels/display.rs` the
decode entry point is `decode_image_and_emit` at line 1251.
Decompression dispatch (`decompress_glz`, `decompress_spice_lz4`,
`decode_mjpeg_frame`, raw `Pixmap` copy) happens between the
`ImageDescriptor` read at line 1293 and the `record_decode`
call at line 1672. Wrap the decompression dispatch with an
`Instant::now()` / `elapsed()`. For `FromCache` and for early
returns that never invoke the decoder, the duration is 0.

### Aggregate decode stats on DisplaySnapshot

Add to `DisplaySnapshot` in
`shakenfist-spice-renderer/src/snapshots.rs:34-59`:

```rust
/// Total decodes recorded since session start. Counts every
/// `record_decode` call, including failures and cache hits.
pub decode_total_count: u64,
/// Decodes where `success == false`. Subset of the total.
pub decode_failed_count: u64,
/// Decodes where `from_cache == true`. Subset of the total.
pub decode_from_cache_count: u64,
/// Min / max / mean of `decode_duration_us` over the entries
/// currently in `recent_decodes` for which the decoder ran
/// (i.e. excluding cache hits and short-circuited failures).
/// Zero when the ring contains no such entries.
pub decode_recent_min_us: u32,
pub decode_recent_max_us: u32,
pub decode_recent_mean_us: u32,
```

The two cumulative counters live on the channel struct and
increment in `record_decode`. The three recent-window stats
are computed at snapshot-emit time over the cloned ring in
`update_snapshot` (`display.rs:2120-2150`) — no extra state.

### Socket-read fill stats

`run_loop` in `display.rs:599-642` reads up to 256 KB into a
stack-allocated `chunk: [u8; 262144]` and then processes the
`&chunk[..n]` slice. When `n == chunk.len()` the OS recv buffer
had at least one full chunk waiting at read time, which is a
cheap signal that the read loop is not consuming faster than
the server is sending.

Add to the channel struct (and mirror onto `DisplaySnapshot`):

```rust
/// Total reads from the socket since session start. One per
/// `s.read(&mut chunk).await` that returned `n > 0`.
pub socket_read_count: u64,
/// Reads where `n == 262144` (the full chunk size). A high
/// ratio of these to `socket_read_count` indicates the OS recv
/// buffer was usually non-empty when we read — i.e. the read
/// loop is not keeping up with arrival rate.
pub socket_reads_at_chunk_cap: u64,
/// Largest `n` observed over the session. Useful as a sanity
/// check when chunk capacity is later tuned.
pub socket_max_chunk_bytes: u32,
```

Update at `display.rs:619` and following (after the `read`
returns and before the chunk is processed). FIONREAD-style
direct buffer inspection is rejected: it requires raw-fd access
to a tokio stream and adds a per-platform code path, and the
fill-rate proxy answers the diagnostic question well enough.

### ACK-send stats

`process_messages` at `display.rs:666-700` calls
`self.send_ack()` whenever `message_count - last_ack >=
ack_window`. The snapshot already exposes `ack_window`,
`message_count`, and `last_ack`, but a static snapshot can't
tell whether ACKs are firing on a healthy cadence or whether a
long pause has accumulated unACKed messages. Add:

```rust
/// Total ACK messages sent to the server since session start.
pub ack_send_count: u32,
/// Session-relative seconds of the most recent ACK send. None
/// until the first ACK is sent.
pub last_ack_send_ts_secs: Option<f64>,
/// Intervals (seconds) between the last N consecutive ACK
/// sends, oldest first. Bounded ring; see RECENT_ACK_INTERVALS_CAP.
pub recent_ack_intervals_secs: VecDeque<f64>,
```

Cap the ring at 32. The math: with a typical `ack_window` of
1000 messages and a busy display session at thousands of msgs/s,
32 intervals is roughly the last 10–30 seconds of activity —
enough to see whether ACKs paused without bloating the snapshot.
Define a constant alongside `MAX_RECENT_DECODES` in `display.rs`:

```rust
const RECENT_ACK_INTERVALS_CAP: usize = 32;
```

Increment and record in `send_ack` (or in `process_messages` at
the call site, whichever already owns the timing).

### Bug-report flow

`DisplaySnapshot` derives `Serialize` (`snapshots.rs:34`) and is
serialized to `channel-state.json` via
`ChannelSnapshots::snapshot_json_for("display")` at
`ryll/src/bugreport.rs:260-292`. New fields therefore flow into
the bug report without any change to the bug-report code path.

The existing test `test_display_snapshot_serialises` at
`ryll/src/bugreport.rs:1853` covers serialisation shape and
needs to be extended to assert the new fields are present.

## Steps

### Step 1: Extend `DecodeResult` and `DisplaySnapshot`

1. In `shakenfist-spice-renderer/src/snapshots.rs`:
   - Add `decode_duration_us: u32` to `DecodeResult`.
   - Add `decode_total_count`, `decode_failed_count`,
     `decode_from_cache_count`, `decode_recent_{min,max,mean}_us`,
     `socket_read_count`, `socket_reads_at_chunk_cap`,
     `socket_max_chunk_bytes`, `ack_send_count`,
     `last_ack_send_ts_secs`, `recent_ack_intervals_secs` to
     `DisplaySnapshot`.
   - Update the `Default` derive — `VecDeque` and numeric types
     default to empty/zero so the derive still works.

### Step 2: Time the decode in `decode_image_and_emit`

1. In `shakenfist-spice-renderer/src/channels/display.rs`:
   - At the start of `decode_image_and_emit` (around line 1251)
     do **not** start the timer yet; we only want to time the
     decompression work, not the header parsing.
   - Once `image_type` is resolved (around line 1294) and just
     before the decompression dispatch, capture
     `let decode_start = std::time::Instant::now();`.
   - After the decompression dispatch produces `decompressed`
     (around line 1668, just before the `record_decode` call at
     1672), compute
     `let decode_duration_us = decode_start.elapsed().as_micros() as u32;`
     saturating at `u32::MAX` (use `try_into` with
     `unwrap_or(u32::MAX)`).
   - For `FromCache` and for early returns that never invoke
     the decoder, `decode_duration_us` is `0`.
   - Pass the value into the `DecodeResult` literal at 1672.

### Step 3: Aggregate decode counts and recent-window stats

1. Add fields to the channel struct (mirroring the snapshot):
   `decode_total_count: u64`, `decode_failed_count: u64`,
   `decode_from_cache_count: u64`.
2. In `record_decode` (`display.rs:2113`), increment the
   counters from the `DecodeResult` flags.
3. In `update_snapshot` (`display.rs:2120-2150`), after cloning
   `recent_decodes` into `snap`, iterate the ring and compute
   `min / max / mean` of `decode_duration_us` over entries where
   `!from_cache && success`. Empty result → all three are 0.

### Step 4: Socket-read fill stats

1. Add fields to the channel struct: `socket_read_count: u64`,
   `socket_reads_at_chunk_cap: u64`,
   `socket_max_chunk_bytes: u32`.
2. In `run_loop` (`display.rs:619+`), after the `if n == 0`
   check and before `self.byte_counter.add(...)`:
   - `self.socket_read_count += 1;`
   - `if n == chunk.len() { self.socket_reads_at_chunk_cap += 1; }`
   - `self.socket_max_chunk_bytes =
       self.socket_max_chunk_bytes.max(n as u32);`
3. Mirror to the snapshot in `update_snapshot`.

### Step 5: ACK-send stats

1. Add fields to the channel struct: `ack_send_count: u32`,
   `last_ack_send_ts_secs: Option<f64>`,
   `recent_ack_intervals_secs: VecDeque<f64>`.
2. Define `const RECENT_ACK_INTERVALS_CAP: usize = 32;` near
   `MAX_RECENT_DECODES`.
3. At the existing ACK call site (`display.rs:693-694`) — or in
   `send_ack` itself if that keeps the math local — after a
   successful ACK send:
   - Compute `now = self.traffic.elapsed().as_secs_f64()`.
   - If `last_ack_send_ts_secs` is `Some(prev)`, push `now - prev`
     into `recent_ack_intervals_secs`; pop the front if length
     exceeds `RECENT_ACK_INTERVALS_CAP`.
   - Set `last_ack_send_ts_secs = Some(now)`.
   - `ack_send_count += 1`.
4. Mirror to the snapshot in `update_snapshot` (clone the ring).

### Step 6: Tests

1. Extend `test_display_snapshot_serialises` at
   `ryll/src/bugreport.rs:1853` to set the new fields on a
   `DisplaySnapshot` and assert they survive serialisation.
2. Add a unit test for the recent-window aggregation: build a
   `VecDeque<DecodeResult>` with mixed cache-hit / failure /
   success entries and assert that the min/max/mean computation
   excludes cache hits and failures.
3. Add a unit test for the ACK-interval ring: push 40 intervals
   and assert that only the most recent 32 are retained, in
   order.

### Step 7: Manual verification

1. `make build` and `make test` succeed.
2. `make lint` (or `pre-commit run --all-files`) passes.
3. Run ryll against a SPICE server, trigger a bug report after
   a few seconds of activity, unzip it, and verify
   `channel-state.json` for the display channel contains all
   new fields with plausible values:
   - `decode_total_count > 0`.
   - `decode_recent_max_us > decode_recent_min_us` on a busy
     session, both `> 0`.
   - `socket_read_count > 0`,
     `socket_max_chunk_bytes <= 262144`.
   - `ack_send_count > 0` after enough traffic to exceed an
     ACK window, with a non-empty `recent_ack_intervals_secs`.

### Step 8: Documentation

1. Update `docs/troubleshooting.md` with a short section
   describing the new fields and how to read them when
   diagnosing "video not keeping up" reports. Cross-reference
   the master plan.
2. Mention the new fields in `ARCHITECTURE.md`'s description
   of `DisplaySnapshot` (if such a description exists there).
3. No `README.md` update needed — the diagnostic fields are
   internal to bug reports, not user-facing.

## Administration and logistics

### Success criteria

- `DecodeResult` carries `decode_duration_us` and the field
  appears in `channel-state.json` for every recorded decode.
- `DisplaySnapshot` exposes cumulative decode counters,
  recent-window min/max/mean decode duration, socket-read fill
  stats, and ACK-send stats.
- `recent_ack_intervals_secs` is capped at 32 entries.
- All new fields default to zero / empty so existing tests that
  build a default `DisplaySnapshot` keep passing.
- `make build`, `make test`, and `make lint` (or
  `pre-commit run --all-files`) all pass.
- A bug report generated during a session with display traffic
  contains plausible values for every new field.

### Risks

- **Per-decode `Instant::now()` cost.** `Instant::now()` on
  Linux is a `vDSO`-fast `clock_gettime` call (tens of ns).
  Two of them per decode at a few thousand decodes per second
  is below the noise floor of the work we're already doing.
- **`as u32` truncation on slow decodes.** A decode taking
  >71 minutes would saturate. Such a case is itself a bug
  worth seeing; `unwrap_or(u32::MAX)` makes it visible rather
  than crashing.
- **ACK-ring memory.** 32 × `f64` = 256 bytes per session.
  Negligible.
- **Snapshot clone cost.** `update_snapshot` already clones
  the `recent_decodes` ring (cap `MAX_RECENT_DECODES = 20`)
  on every call; adding a 32-entry
  `recent_ack_intervals_secs` clone is negligible.
- **Backward compatibility of `channel-state.json`.** The file
  is internal to bug reports and parsed by maintainers, not by
  external tooling, so adding fields is safe.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
