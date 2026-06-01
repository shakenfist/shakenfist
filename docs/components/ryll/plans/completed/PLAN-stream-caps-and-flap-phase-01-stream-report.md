# Phase 1 — STREAM_REPORT

Phase 1 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
master plan and the relevant source files; ground your
answers in what the code actually does today. For SPICE
protocol details, cross-check against the reference sources
at `/srv/src-reference/spice/` cited inline.

## Goal

Advertise and use `SPICE_DISPLAY_CAP_STREAM_REPORT = 4` so the
spice-server's video encoder can adapt bitrate based on
client-side feedback. Receive `SPICE_MSG_DISPLAY_STREAM_ACTIVATE_REPORT`
and reply with periodic `SPICE_MSGC_DISPLAY_STREAM_REPORT`
(client opcode 102) matching spice-gtk's send semantics.

Stretch outcome (visible in `channel-state.json` after this
lands): per-stream `report_*` fields populated when a stream
goes through its life-cycle, so a future bug report can show
both "server activated reports for this stream" and "client
sent N reports back with these frame/drop counts".

## Scope

In scope:
- `display_client::STREAM_REPORT = 102` opcode constant.
- `capabilities::DISPLAY_STREAM_REPORT = 1 << 4` constant and
  inclusion in `DEFAULT_DISPLAY`.
- `STREAM_ACTIVATE_REPORT` payload parsing (currently a no-op
  `debug!` at `shakenfist-spice-renderer/src/channels/display.rs:1317`).
- A shared, monotonic mm_time clock fed by the main channel
  (`SPICE_MSG_MAIN_INIT::multi_media_time` and
  `SPICE_MSG_MAIN_MULTI_MEDIA_TIME` at
  `shakenfist-spice-renderer/src/channels/main_channel.rs:749-763`),
  readable by the display channel for "now in mm_time".
- Parsing `multi_media_time` out of `STREAM_DATA` /
  `STREAM_DATA_SIZED` (currently skipped at
  `shakenfist-spice-renderer/src/channels/display.rs:1163-1170`
  for STREAM_DATA and `:1146-1162` for STREAM_DATA_SIZED).
- Per-stream report state on `StreamState` and trigger predicate
  matching spice-gtk:
  `report_num_frames >= max_window_size` OR
  `now_mm_time - report_start_time >= timeout_ms` OR
  `report_drops_seq_len >= 3` (spice-gtk constant
  `STREAM_REPORT_DROP_SEQ_LEN_LIMIT` at
  `/srv/src-reference/spice/spice-gtk/src/channel-display.c:1532`).
- `STREAM_REPORT` marshalling: 8 × u32 LE per
  `/srv/src-reference/spice/spice-common/common/messages.h`
  (and `spice.proto:1004-1026`).
- New `StreamSnapshot` fields surfaced in bug reports
  (see *Snapshot additions* below).
- Unit tests for the trigger predicate and the wire-format
  serializer.

Out of scope for this phase:
- Real `audio_delay` computation — phase 1 always sends
  `UINT32_MAX`. Threading playback-latency state through to the
  display channel is a follow-up worth its own change.
- The "unsupported codec" special-case signal
  (`num_frames == 0, num_drops == UINT32_MAX`) for orphan
  STREAM_DATA. We count orphans but don't reply yet. The signal
  matters when we accept multi-codec streams (phase 6), so we
  fold it in there alongside H.264 decode failures.
- Adaptive UI surfacing of report cadence — `channel-state.json`
  is enough for now.
- Cross-validation against virt-viewer / spice-gtk. The phase 6
  plan owns that test; we don't need it for STREAM_REPORT
  alone.

## Open questions

- **Q1 (decide now):** Where does the mm_time clock live?
  *Decision*: introduce a new lightweight shared structure
  `MmClock` (or extend an existing `Arc<Mutex<...>>` that is
  already plumbed everywhere). The cleanest hook is a new
  `Arc<MmClock>` constructed alongside the existing channel
  snapshots in `session.rs` and passed into both `MainChannel`
  (writer) and `DisplayChannel` (reader). The reader path needs
  no locking on the hot path beyond a single load if `MmClock`
  exposes `now_mm_time(&self) -> u32` that combines a stored
  base mm_time with `Instant::now().duration_since(base_instant)`.
  Spec: see *Design notes / mm_time*.
- **Q2 (decide now):** Where is the trigger predicate evaluated?
  *Decision*: inline at the end of the `STREAM_DATA` /
  `STREAM_DATA_SIZED` handler, after updating per-stream
  counters. Mirrors spice-gtk
  (`channel-display.c:1559-1561`). No timer task — the predicate
  is also satisfied by elapsed wall time within the same
  frame-processing path, so we don't need a periodic tick.
  Edge case: if a stream stops receiving frames, the timeout
  branch never fires because there's no event to evaluate it.
  spice-gtk has the same property; the report-window timeout
  only matters when frames are still arriving but slowly. Live
  with it; the server's `STREAM_TIMEOUT = 1 s` will tear the
  stream down anyway.
- **Q3 (decide in implementation):** How wide should the
  per-stream `recent_report_*` ring be? *Default*: just the
  last report's contents (single fields, not a ring). The ring
  of recently-destroyed streams from phase 0 already gives us
  longitudinal data via `last_report_send_count` on the dying
  stream. Add a real ring only if a future bug report shows we
  need it.

## Design notes

### Wire formats

**`SPICE_MSG_DISPLAY_STREAM_ACTIVATE_REPORT` (server → client, opcode 515):**

```text
offset  bytes  field             notes
 0      4      stream_id         u32 LE
 4      4      unique_id         u32 LE   echo this back in our report
 8      4      max_window_size   u32 LE   server hardcodes 5 (RED_STREAM_CLIENT_REPORT_WINDOW)
12      4      timeout_ms        u32 LE   server hardcodes 1000 (RED_STREAM_CLIENT_REPORT_TIMEOUT)
```

Total: 16 bytes. Quoted from
`/srv/src-reference/spice/spice-common/common/messages.h`
and confirmed against `spice-protocol/spice/protocol.h:142`
where `SPICE_DISPLAY_CAP_STREAM_REPORT` is 4.

**`SPICE_MSGC_DISPLAY_STREAM_REPORT` (client → server, opcode 102):**

```text
offset  bytes  field                 notes
 0      4      stream_id             u32 LE
 4      4      unique_id             u32 LE   echo of activate value
 8      4      start_frame_mm_time   u32 LE   mm_time of first frame in window
12      4      end_frame_mm_time     u32 LE   mm_time of last frame in window
16      4      num_frames            u32 LE   total frames processed in window
20      4      num_drops             u32 LE   subset of num_frames that arrived late
24      4      last_frame_delay      i32 LE   end_frame_mm_time - now_mm_time (signed)
28      4      audio_delay           u32 LE   ms; UINT32_MAX when no audio
```

Total: 32 bytes. Special case:
`num_frames == 0 && num_drops == UINT32_MAX` is the "unsupported
codec" wildcard. Server checks this *before* validating
`unique_id` (`/srv/src-reference/spice/spice/server/dcc.cpp:858-865`),
so any 8-tuple matching that shape will instruct the encoder to
self-destroy. Out of scope here.

### mm_time

SPICE `mm_time` is a server-side millisecond counter. The
server seeds the client with an initial value in
`SPICE_MSG_MAIN_INIT::multi_media_time` (we already read this
at `shakenfist-spice-renderer/src/channels/main_channel.rs:656`
but discard it) and periodically broadcasts updates via
`SPICE_MSG_MAIN_MULTI_MEDIA_TIME` (handler exists as a no-op
at line 749-763).

For `STREAM_REPORT.last_frame_delay` we need to compute a
"current mm_time" at send time. The accepted approach (see
spice-gtk `channel-main.c` `mm_time_set` / `_get`) is to store
a `(base_mm_time, base_instant)` pair on update and compute
`base_mm_time + (now - base_instant).as_millis()` on demand.
Wraparound at 2^32 ms (~49.7 days) is acceptable.

Proposed shape (sketch — implementer can adjust):

```rust
// shakenfist-spice-renderer/src/mm_clock.rs (new file)
pub struct MmClock {
    inner: parking_lot::Mutex<MmClockInner>,
}

struct MmClockInner {
    base_mm_time: u32,
    base_instant: Instant,
    set_count: u64,        // bug-report visibility
    last_set_ts_secs: f64,
}

impl MmClock {
    pub fn new() -> Self { /* base = 0, base_instant = Instant::now() */ }
    pub fn set(&self, mm_time: u32, traffic_elapsed_secs: f64) { /* update */ }
    pub fn now(&self) -> u32 { /* base + (now - base_instant).as_millis() */ }
}
```

We don't have `parking_lot` in `shakenfist-spice-renderer`'s
deps today — `std::sync::Mutex` is fine; the lock is held for
nanoseconds. Confirm before pulling in a new dependency.

Plumb the `Arc<MmClock>` through `run_connection` in
`shakenfist-spice-renderer/src/session.rs` so that `MainChannel`
gets a writer handle and `DisplayChannel::new` accepts a reader
handle. Both already take several Arcs, this is one more.

### Per-stream report state additions to `StreamState`

```rust
// channels/display.rs — extend StreamState
report_is_active: bool,
report_unique_id: u32,
report_max_window_size: u32,
report_timeout_ms: u32,
// rolling window counters; reset on each send
report_num_frames: u32,
report_num_drops: u32,
report_drops_seq_len: u32,
report_start_frame_mm_time: u32,
report_end_frame_mm_time: u32,
report_start_now_mm_time: u32,
// cumulative (don't reset)
report_send_count: u32,
last_report_sent_ts_secs: Option<f64>,
// last sent report values (for snapshot)
last_report_num_frames: u32,
last_report_num_drops: u32,
last_report_last_frame_delay: i32,
```

### Trigger predicate (mirroring spice-gtk)

After processing each STREAM_DATA frame for a stream where
`report_is_active == true`:

```text
if report_num_frames == 0:
    report_start_frame_mm_time = frame_mm_time
    report_start_now_mm_time   = now_mm_time
report_num_frames += 1
report_end_frame_mm_time = frame_mm_time
last_frame_delay = (frame_mm_time as i64) - (now_mm_time as i64)
if last_frame_delay < 0:
    report_num_drops += 1
    report_drops_seq_len += 1
else:
    report_drops_seq_len = 0

if report_num_frames >= report_max_window_size
        OR (now_mm_time - report_start_now_mm_time) as i32 >= report_timeout_ms as i32
        OR report_drops_seq_len >= STREAM_REPORT_DROP_SEQ_LEN_LIMIT (= 3):
    send_stream_report(...)
    reset rolling counters
```

The mm_time subtraction is *modular*: `mm_time` wraps at 2^32
ms, so we use `i32`-cast subtraction (spice-gtk's
`spice_mmtime_diff`). Same logic for the `last_frame_delay`
narrowing to `i32`.

`STREAM_REPORT_DROP_SEQ_LEN_LIMIT = 3` belongs as a module-level
constant alongside `MAX_RECENT_DECODES`.

### Snapshot additions

Extend `StreamSnapshot` (in
`shakenfist-spice-renderer/src/snapshots.rs`) with:

```rust
pub report_is_active: bool,
pub report_unique_id: u32,
pub report_max_window_size: u32,
pub report_timeout_ms: u32,
pub report_send_count: u32,
pub last_report_sent_ts_secs: Option<f64>,
pub last_report_num_frames: u32,
pub last_report_num_drops: u32,
pub last_report_last_frame_delay: i32,
```

Existing `streams_active` / `streams_recently_destroyed` paths
automatically carry these into `channel-state.json` once
`stream_state_to_snapshot` copies them across.

Also add to `DisplaySnapshot` aggregate:

```rust
pub stream_reports_sent_total: u64,
pub stream_reports_unsupported_signals_sent: u64,  // for phase 6 — initialised to 0
```

`MainSnapshot` gains:

```rust
pub mm_time_now: u32,
pub mm_time_set_count: u64,
pub last_mm_time_set_ts_secs: Option<f64>,
```

(`mm_time_now` is informational; computed at snapshot time.)

### `STREAM_REPORT` send path

Add a `send_stream_report(&mut self, stream_id: u32, state: &mut StreamState)` method on `DisplayChannel`. Build a 32-byte LE payload, call the existing `send_with_log(display_client::STREAM_REPORT, &msg)` (the function at `display.rs:2376` — needs to be made tolerant of the new opcode in logging).

Logging: extend `shakenfist-spice-protocol/src/logging.rs` so the new opcode prints as `stream_report` (matches the existing `display_server::STREAM_REPORT` / spice-gtk naming).

## Execution step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1A | medium | sonnet | none | **Wire-protocol constants and cap advertisement.** Add `display_client::STREAM_REPORT = 102` to `shakenfist-spice-protocol/src/constants.rs:223-228` and the matching name `"stream_report"` to `shakenfist-spice-protocol/src/logging.rs::display_client` (mirror existing entries). Add `pub const DISPLAY_STREAM_REPORT: u32 = 1 << 4;` to `capabilities` and OR it into `DEFAULT_DISPLAY` at `constants.rs:126-127`. Add the logging-roundtrip test next to the existing `main_server_multi_media_time_const_and_name` test (which asserts opcode + name pair). No behavioural change; only constants. Verify `make build && make test && make lint` pass. |
| 1B | high | opus | none | **MmClock plumbing.** Introduce `shakenfist-spice-renderer/src/mm_clock.rs` with the `MmClock` shape sketched in *Design notes / mm_time* (no `parking_lot` — use `std::sync::Mutex`; the lock is held for nanoseconds). Wire `Arc<MmClock>` through `run_connection` in `shakenfist-spice-renderer/src/session.rs` (one more Arc alongside the existing snapshot ones); pass to `MainChannel::new` and `DisplayChannel::new`. Update `MainChannel` to call `mm_clock.set(...)` from both `SPICE_MSG_MAIN_INIT` (around `main_channel.rs:661` after the init log) and the `MULTI_MEDIA_TIME` handler at `main_channel.rs:749-763` (replacing the current debug-and-discard). Add `MainSnapshot::mm_time_now` (read via `mm_clock.now()` in `MainChannel::update_snapshot`), `mm_time_set_count`, `last_mm_time_set_ts_secs`. Unit-test `MmClock`: set, advance instant via injected clock, assert `now()` matches; wraparound at 2^32 ms; idempotence on `set` with same value. Pure-Rust test, no tokio. Verify `make build && make test && make lint`. **Why opus**: new file, cross-channel plumbing, two writer call sites + new snapshot fields + reader-side helper, plus a clock-wraparound test that's easy to get subtly wrong. |
| 1C | medium | sonnet | none | **Parse mm_time from STREAM_DATA.** In `shakenfist-spice-renderer/src/channels/display.rs:1145-1170`, read `multi_media_time` (u32 LE) at payload offset 4 for both STREAM_DATA and STREAM_DATA_SIZED. Plumb it into the existing `if let Some(stream) = self.streams.get_mut(&stream_id)` body as a new `frame_mm_time: u32`. Don't change behaviour yet — just thread the value down to the MJPEG block; the report logic in step 1E will consume it. Verify the SIZED parser still reads `data_size` at offset 32 (the new mm_time read is at offset 4, the existing reads start at offset 16 for `dest_top`). Verify `make build && make test`. |
| 1D | medium | sonnet | none | **STREAM_ACTIVATE_REPORT handler.** Replace the no-op `debug!` at `shakenfist-spice-renderer/src/channels/display.rs:1317`. Parse the 16-byte payload: `(stream_id, unique_id, max_window_size, timeout_ms)`. Look up the matching `StreamState` and set `report_is_active = true; report_unique_id = unique_id; report_max_window_size = max_window_size; report_timeout_ms = timeout_ms`. Reset rolling counters to zero. Log at INFO: `"display: stream_activate_report: id={} unique_id={} window={} timeout_ms={}"`. If no matching stream, log a `warn!` (mirrors the existing `stream_data` orphan path) and otherwise no-op. Extend `StreamState` (display.rs:136) and `StreamSnapshot` (snapshots.rs) with the new fields per *Design notes / Per-stream report state additions* and *Snapshot additions*; default all to zero / false / None. Update `stream_state_to_snapshot` (display.rs) to copy them. Update the snapshot-serialisation test in `ryll/src/bugreport.rs::test_display_snapshot_serialises` to populate and assert each new field. Verify `make build && make test && make lint`. |
| 1E | high | opus | none | **Per-frame report counters and trigger predicate.** In `display.rs::STREAM_DATA` handler (after step 1D & 1C), once `frame_mm_time` is in scope and `stream.report_is_active` is true, update the rolling counters per the trigger pseudocode in *Design notes / Trigger predicate*. Use modular `i32` subtraction throughout: `(a as i64).wrapping_sub(b as i64) as i32`. Introduce `const STREAM_REPORT_DROP_SEQ_LEN_LIMIT: u32 = 3;` near `MAX_RECENT_DESTROYED_STREAMS`. Evaluate the predicate after updates; if true, call `self.send_stream_report(stream_id).await?` (built in step 1F) and reset rolling counters. **Important**: the predicate is evaluated *whether or not* MJPEG decode succeeded — a dropped or undecodable frame still counts toward `report_num_frames`. Add a focused unit test that drives the predicate with a struct-of-counters helper to assert each of the three OR branches independently (window, timeout, drop-seq). **Why opus**: the state machine is small but every field has a subtle "reset on send" / "carry across frames" boundary; spice-gtk's implementation took several years to settle. Getting wraparound and signed handling right matters. |
| 1F | high | opus | none | **STREAM_REPORT marshal + send.** Add `async fn send_stream_report(&mut self, stream_id: u32) -> Result<()>` on `DisplayChannel`. Build the 32-byte LE payload per *Design notes / Wire formats*. Use `last_frame_delay` computed at send time as `(report_end_frame_mm_time as i64).wrapping_sub(mm_clock.now() as i64) as i32`. `audio_delay = u32::MAX` always for now (out of scope). Use the existing `send_with_log(display_client::STREAM_REPORT, &msg)` send path; the logging name was added in step 1A. After send: increment `stream.report_send_count`, set `last_report_sent_ts_secs`, copy `report_num_frames` / `report_num_drops` / `last_frame_delay` into the `last_report_*` mirrors, then zero the rolling counters. Also bump `self.stream_reports_sent_total`. Add a wire-format round-trip unit test: build a known-input report, parse back the 32 bytes, assert every field. Use the same little-endian helpers the rest of the channel does. Verify `make build && make test && make lint`. **Why opus**: send path mirrors several existing channels but the post-send reset is easy to get wrong; the wire-format test must be exact. |
| 1G | low | haiku | none | **Snapshot test fields + aggregate counter.** Extend `DisplaySnapshot` with `stream_reports_sent_total: u64` and `stream_reports_unsupported_signals_sent: u64` (the latter initialised to 0 — written in phase 6). Wire both through `update_snapshot` in `display.rs`. Extend the `test_display_snapshot_serialises` test in `ryll/src/bugreport.rs` to assert these appear in the JSON output. Verify `make build && make test && make lint`. |

Commits: one per step (1A through 1G). The commit-message
template at the repo root should be used; each commit message
includes the `Prompt:` paragraph summarising the step's brief,
and the standard `Co-Authored-By`/`Signed-off-by` block.

## Test plan

Manual / integration (after all steps):

1. **Server logs STREAM_REPORT receipt.** Connect ryll against
   a SPICE server with `--debug` on the server (or equivalent
   QEMU verbose flag), trigger a video stream, confirm the
   server logs a STREAM_REPORT arriving.
2. **Bug-report snapshot shows live state.** During an active
   stream, file a Display bug report. `channel-state.json`
   should contain a non-empty `streams_active` entry with
   `report_is_active: true`, `report_send_count > 0`, and
   sensible `last_report_*` values.
3. **Spice-gtk parity smoke.** Run wireshark against ryll
   versus spice-gtk against the same server during identical
   playback. Compare STREAM_REPORT payloads side-by-side —
   field values should be within a reasonable range of each
   other (drop counts may differ, but `unique_id` echoing and
   `start_frame_mm_time` / `end_frame_mm_time` ordering should
   match).

Automated:

- `MmClock` unit tests in `mm_clock.rs`: set/get, advance,
  wraparound at 2^32 ms.
- Trigger-predicate unit tests in `channels/display.rs`'s
  `mod tests`: drives a struct with the rolling counters and
  asserts each OR branch independently fires.
- Wire-format round-trip unit test for the 32-byte payload.
- Existing snapshot-serialisation test extended (step 1G).

## Documentation impact

`ARCHITECTURE.md` lists the display channel capabilities; this
phase adds `STREAM_REPORT (4)` to that table. Strictly speaking
that's phase 10's job, but the implementing agent for step 1A
should note the entry needs updating later if it edits docs
adjacent to the cap list.

## Success criteria

- `display_client::STREAM_REPORT = 102` and
  `capabilities::DISPLAY_STREAM_REPORT = 1 << 4` exist as
  constants; `DEFAULT_DISPLAY` includes the bit.
- Receiving `STREAM_ACTIVATE_REPORT` populates the
  corresponding `StreamState`.
- Within a video playback session, ryll sends
  `STREAM_REPORT` messages on the spice-gtk-compatible
  trigger conditions, visible to wireshark.
- `channel-state.json` shows `streams_active[*].report_*`
  fields populated for live streams and frozen-at-destroy for
  entries in `streams_recently_destroyed`.
- `make test` covers `MmClock`, the trigger predicate, and the
  wire format.
- `pre-commit run --all-files` clean.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the operator with their
understanding of the step, the files they intend to touch,
and any deviations from the brief.
