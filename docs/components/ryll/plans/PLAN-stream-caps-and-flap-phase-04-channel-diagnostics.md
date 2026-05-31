# Phase 4 — Channel diagnostics audit + playback observability

Phase 4 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
master plan, the existing snapshot definitions
(`shakenfist-spice-renderer/src/snapshots.rs`), each per-channel
module under `shakenfist-spice-renderer/src/channels/`, and
the bug-report writer (`ryll/src/bugreport.rs`). Ground your
answers in what the code does today. For audio specifics, read
the `playback.rs` audio-thread implementation carefully — the
`cpal` callback path is where the silence-during-video symptom
lives, and snapshot writes from a callback thread require care.
Flag any uncertainty explicitly rather than guessing.

## Goal

Make every active SPICE channel surface enough state in a bug
report that an operator can characterise its current behaviour
without re-running the session with extra logging. Today the
display channel meets this bar (40 snapshot fields, post-phase-3);
main, inputs, and cursor meet it adequately; **playback,
usbredir, and webdav publish only the eight-field generic
transport baseline and nothing channel-specific.** Driven by
sessions 002d/002e where the user reported audio went silent
during video playback and the console logs told us nothing
beyond "audio output started" — the bug report had no packet
counts, no decoder errors, no underrun stats to confirm or
rule out the hypotheses (CoreAudio routing? buffer underrun?
server stopped sending DATA?).

Concretely, after this phase:

- `PlaybackSnapshot` includes per-packet, per-decode, and
  per-device-pull counters sufficient to distinguish "server
  stopped sending DATA" from "we received DATA but failed to
  decode" from "we decoded fine but the device callback
  starved" from "callback ran but pushed silence."
- `UsbredirSnapshot` includes USB-side packet counts and the
  list of currently-redirected devices.
- `WebdavSnapshot` includes HTTP request counts and
  per-session activity timestamps.
- An audit doc captures the audit matrix and the "minimum
  diagnostic baseline" every channel snapshot should publish,
  so the next channel we add starts at the right level.
- The bug-report `channel-state.json` dispatch and the
  serialisation regression tests are extended to cover the
  new fields.

## Scope

In scope:

- **Audit doc** — `docs/channel-diagnostics-audit.md` (new) with
  the per-channel matrix, missing-fields list, and the agreed
  "minimum baseline" definition. Lightweight, intended as a
  living checklist when new channels are added.
- **PlaybackSnapshot expansion** (the urgent piece): see
  *PlaybackSnapshot fields* below.
- **UsbredirSnapshot expansion**: USB packet counts (server →
  client + client → server), bytes by direction, active
  redirected device count + per-device metadata (vendor, product,
  device class, attached_at_secs), last add/remove timestamps.
- **WebdavSnapshot expansion**: HTTP request count, response
  bytes shipped, active spice-vmc session count, last activity
  timestamps per session.
- **Bug-report serialiser tests** extended for each updated
  snapshot, mirroring the
  `test_display_snapshot_serialises` pattern.
- **Snapshot-writer plumbing**: where a channel's protocol
  handler runs on a tokio task, the snapshot Mutex is already
  held the same way display does it (lock → mutate → drop).
  Playback's audio thread is the exception (cpal callback,
  native thread) — needs an atomic-counter pattern to feed
  the snapshot without taking the Mutex on every device pull.

Out of scope:

- **Record channel implementation.** We currently `Skipping
  channel: record` at link time (`main_channel.rs`). The
  decision is to keep skipping it (no UI surface for mic
  capture today) and add a one-line comment in the channel-list
  handler explaining why. No `RecordSnapshot` needed.
- **Pretty-printing the new fields in the live stats panel.**
  This phase is about bug-report observability, not the in-app
  UI. Live-panel work can come later if the audit doc
  identifies signals worth showing operators in real time.
- **Diagnosing the actual 002d/002e audio-silence root cause.**
  This phase ships the *instrumentation*; running another
  session with that instrumentation in place is the diagnostic
  step that follows, and likely lands as a separate small
  follow-up commit.
- **Inputs / cursor / main snapshots.** They already meet the
  baseline; the audit doc captures what they have and confirms
  they don't need expansion in this phase.

## Open questions

- **Q1 (decide in 4A audit): what is the "minimum diagnostic
  baseline"?** Working proposal:
  - Transport common (already universal): `bytes_in`,
    `bytes_out`, `last_recv_ts_secs`, `last_send_ts_secs`,
    `ping_recv_count`, `pong_send_count`,
    `last_ping_recv_ts_secs`, `writer_dropped_count`.
  - **New baseline additions every channel should add**:
    - `messages_recv_by_opcode: BTreeMap<u16, u64>` — per-opcode
      receive counts. One line per server-defined opcode the
      channel actually sees. Cheap (a single match-arm `+= 1`
      per message); gives a bug report a complete picture of
      what the server has sent on this channel since session
      start.
    - `messages_send_by_opcode: BTreeMap<u16, u64>` — same for
      sends.
    - `last_unknown_opcode: Option<u16>` +
      `unknown_opcode_count: u64` — surfaces server-side
      protocol bugs (or our own coverage gaps) that
      `warn_once` currently swallows quietly.
    - `recent_action_ring: VecDeque<ActionEntry>` — bounded
      ring (cap ~16) of structured "interesting event" rows
      with a timestamp, action kind, and one optional string
      field per channel. Display's existing
      `recent_decodes` is the precedent.
  - Per-channel modules then add their own channel-specific
    fields on top of that. **Decision needed in 4A**: do we
    define a `pub struct ChannelTransportBaseline { ... }`
    embedded in each snapshot via `#[serde(flatten)]`, or do
    we keep the fields inline per snapshot (less DRY but no
    serde-flatten footgun)? Recommend inline per snapshot for
    now (consistent with today), promote to a shared struct
    only if the duplication becomes painful.

- **Q2 (decide in 4B): how does the cpal audio callback feed
  the snapshot?** The callback runs on a native thread owned
  by cpal — `tokio::sync::Mutex` is wrong, and `std::sync::Mutex`
  would block the device thread on contention which is
  unacceptable. Working proposal:
  - Audio thread maintains a small `AtomicU64` set
    (`samples_consumed_total`, `device_callbacks_total`,
    `underrun_total`) it can write lock-free on every
    callback.
  - The tokio-side `update_snapshot` reads those atomics into
    the snapshot under the Mutex (single load each, no
    contention). Same pattern as today's
    `client_keepalive_send_count` on main, just more counters.
  - For the audio-session metadata (sample rate, channels,
    codec, last_start mm_time) the tokio side already has all
    of it — no need for shared state.

- **Q3 (decide in 4C — playback step): which underrun
  signal do we trust?** Two candidate sources:
  - cpal's `OutputCallbackInfo` provides
    `timestamp.playback - timestamp.callback` slack; negative
    or zero is a near-underrun.
  - Our own ring-buffer: if `consumer.slots() == 0` at the
    start of a callback, we definitely underran.
  - **Recommend**: the ring-buffer check is the authoritative
    signal — it directly answers "did we hand the device
    silence because we had nothing decoded?" The cpal
    timestamp slack is a nice-to-have for next phase.

- **Q4 (decide in 4D — usbredir step): how do we enumerate
  redirected devices?** The current usbredir code path
  receives `usb_redir_device_connect` /
  `usb_redir_device_disconnect` packets from the server-side
  usbredir-host. We don't expose a guest-USB view. The list
  of currently-redirected devices is per-channel state we
  already have (we tracked which devices we sent connect for)
  — surface that set. If we don't currently track it, this
  phase adds the tracking.

- **Q5 (open): should `messages_recv_by_opcode` live on every
  channel snapshot or as a single cross-channel map on a new
  `ProtocolSnapshot`?** Per-channel is less work and easier to
  read in a bug report (you see playback's opcodes next to
  playback's other state). Cross-channel makes filtering
  easier for tooling. **Defer**: ship per-channel in this
  phase, revisit if/when tooling pressure shows up.

## Design notes

### Today's audit matrix (snapshot of state at plan-write time)

| Channel | Fields | Channel-specific signals | Gap |
|---|---|---|---|
| display | 40 | rich (post-phase-3) | none |
| main | 15 | mm_time, session_id, keepalive_timeout_fired | OK |
| inputs | 14 | motion_count, recent_events | OK |
| cursor | 14 | cache_entries, cache_contents | OK |
| **playback** | 8 | **none** | **all audio-side state** |
| **usbredir** | 8 | **none** | **all USB-side state** |
| **webdav** | 8 | **none** | **all HTTP-side state** |
| record | 0 | channel skipped at link time | document the skip |

### PlaybackSnapshot fields (target shape after step 4C)

```rust
pub struct PlaybackSnapshot {
    // --- transport common (unchanged) ---
    pub bytes_in: u64,
    pub bytes_out: u64,
    pub last_recv_ts_secs: Option<f64>,
    pub last_send_ts_secs: Option<f64>,
    pub ping_recv_count: u32,
    pub pong_send_count: u32,
    pub last_ping_recv_ts_secs: Option<f64>,
    pub writer_dropped_count: u32,

    // --- baseline additions (per Q1) ---
    pub messages_recv_by_opcode: std::collections::BTreeMap<u16, u64>,
    pub messages_send_by_opcode: std::collections::BTreeMap<u16, u64>,
    pub last_unknown_opcode: Option<u16>,
    pub unknown_opcode_count: u64,

    // --- per-session audio state ---
    /// Set on every SPICE_MSG_PLAYBACK_START. None when no
    /// session has been started or the most recent session has
    /// been STOPped.
    pub current_session: Option<PlaybackSessionInfo>,
    /// Monotonic count of START messages observed.
    pub start_count: u64,
    /// Monotonic count of STOP messages observed.
    pub stop_count: u64,

    // --- audio-data plumbing counters ---
    /// Count of SPICE_MSG_PLAYBACK_DATA packets received.
    pub data_packets_received: u64,
    /// Count of DATA packets successfully decoded by the
    /// active codec path (Opus or raw passthrough).
    pub data_packets_decoded: u64,
    /// Count of DATA packets that failed to decode.
    pub data_packets_decode_failed: u64,
    /// Bytes of compressed audio received (sum of DATA
    /// message payload lengths since session start).
    pub data_bytes_received: u64,
    /// Bytes of decoded PCM samples produced.
    pub pcm_bytes_produced: u64,
    /// Recent decode-duration ring (microseconds, cap 64).
    pub recent_decode_durations_us: std::collections::VecDeque<u32>,

    // --- device-side pipeline counters (from audio thread atomics) ---
    /// Count of cpal output callbacks invoked since the
    /// most recent device open.
    pub device_callbacks_total: u64,
    /// Count of callbacks where the ring buffer had zero
    /// slots ready at callback entry (true underruns: we
    /// handed the device silence).
    pub device_underrun_count: u64,
    /// Count of times we attempted to push decoded samples
    /// into the ring buffer and dropped because the ring
    /// was full (encoder ahead of consumer; suggests the
    /// device clock has stopped).
    pub ring_overflow_count: u64,
    /// Samples consumed by the device since most recent
    /// device open (per-channel count; multiply by channel
    /// count for frames).
    pub samples_consumed_total: u64,

    // --- last server-controlled audio params we got ---
    pub last_volume_per_channel: Vec<u16>,
    pub last_mute: Option<bool>,
    pub last_latency_ms: Option<u32>,
}

pub struct PlaybackSessionInfo {
    pub started_at_secs: f64,
    pub mm_time_at_start: u32,
    pub sample_rate_hz: u32,
    pub channels: u8,
    pub codec: PlaybackCodec,
}

pub enum PlaybackCodec {
    Raw,
    Opus,
    Other(u16),
}
```

### UsbredirSnapshot fields (target shape after step 4D)

```rust
pub struct UsbredirSnapshot {
    // --- transport common (unchanged 8 fields) ---
    pub bytes_in: u64,
    pub bytes_out: u64,
    pub last_recv_ts_secs: Option<f64>,
    pub last_send_ts_secs: Option<f64>,
    pub ping_recv_count: u32,
    pub pong_send_count: u32,
    pub last_ping_recv_ts_secs: Option<f64>,
    pub writer_dropped_count: u32,

    // --- baseline additions ---
    pub messages_recv_by_opcode: std::collections::BTreeMap<u16, u64>,
    pub messages_send_by_opcode: std::collections::BTreeMap<u16, u64>,
    pub last_unknown_opcode: Option<u16>,
    pub unknown_opcode_count: u64,

    // --- USB-redirection specifics ---
    /// Currently-active redirected devices (one entry per
    /// usb_redir_device_connect we have sent without a
    /// matching device_disconnect).
    pub redirected_devices: Vec<RedirectedDevice>,
    pub device_connect_total: u64,
    pub device_disconnect_total: u64,
    pub last_device_event_ts_secs: Option<f64>,

    // --- protocol caps observed at handshake ---
    pub server_caps: u32,
    pub client_caps: u32,
}

pub struct RedirectedDevice {
    pub vendor_id: u16,
    pub product_id: u16,
    pub device_class: u8,
    pub attached_at_secs: f64,
    pub bytes_to_guest: u64,
    pub bytes_from_guest: u64,
}
```

### WebdavSnapshot fields (target shape after step 4E)

```rust
pub struct WebdavSnapshot {
    // --- transport common (unchanged) ---
    pub bytes_in: u64,
    pub bytes_out: u64,
    pub last_recv_ts_secs: Option<f64>,
    pub last_send_ts_secs: Option<f64>,
    pub ping_recv_count: u32,
    pub pong_send_count: u32,
    pub last_ping_recv_ts_secs: Option<f64>,
    pub writer_dropped_count: u32,

    // --- baseline additions ---
    pub messages_recv_by_opcode: std::collections::BTreeMap<u16, u64>,
    pub messages_send_by_opcode: std::collections::BTreeMap<u16, u64>,
    pub last_unknown_opcode: Option<u16>,
    pub unknown_opcode_count: u64,

    // --- HTTP / webdav specifics ---
    pub http_requests_received: u64,
    pub http_response_bytes_sent: u64,
    pub active_session_count: u32,
    pub last_request_ts_secs: Option<f64>,
    pub last_response_ts_secs: Option<f64>,
    pub decompressed_size_limit_exceeded_count: u64,
}
```

### MainSnapshot, InputsSnapshot, CursorSnapshot

No field additions in this phase. The audit doc (step 4A)
should record what each has and confirm it meets the
baseline. Specifically:

- **main**: already has session_id, mm_time tracking,
  keepalive bookkeeping. Add to audit: a note that
  `messages_recv_by_opcode` could go here too, but the
  agent-related opcodes (VD_AGENT_*) are nested under
  main's payload framing and a flat opcode map is the wrong
  abstraction. Defer.
- **inputs**: already has motion_count and recent_events
  which is the right per-channel signal. OK.
- **cursor**: cache_entries + cache_contents is the right
  signal. OK.

## Execution step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4A | low | sonnet | none | **Audit doc.** Create `docs/channel-diagnostics-audit.md` containing: (a) the matrix table from *Design notes / Today's audit matrix* above, (b) the per-channel "what would I want in a bug report" wish-list (use the *Design notes* per-snapshot sections as the source, paraphrased into wish-list form), (c) the "minimum diagnostic baseline" decision from Q1 (default to the per-channel-inline form unless implementer finds a cleaner option). Cross-link from `docs/index.md` under the docs index. Verify `pre-commit run --all-files`. |
| 4B | medium | sonnet | none | **Baseline additions for inputs + cursor + main.** Add `messages_recv_by_opcode`, `messages_send_by_opcode`, `last_unknown_opcode`, `unknown_opcode_count` to `InputsSnapshot`, `CursorSnapshot`, and `MainSnapshot`. Wire the per-opcode increments into each channel's message-dispatch match arm (look for existing `match header.message_type` sites). Treat any opcode not handled by an existing arm as "unknown" — bump `unknown_opcode_count` and overwrite `last_unknown_opcode`. Extend the relevant `test_*_snapshot_serialises` tests (or `test_display_snapshot_serialises` style equivalents) so each new field is asserted-present in JSON. Verify `make build && make test && make lint`. |
| 4C | high | opus | none | **PlaybackSnapshot expansion + atomic-counter plumbing.** Implement the full `PlaybackSnapshot` target shape from *Design notes / PlaybackSnapshot fields*. Key decisions in-brief: per Q2, use `AtomicU64` counters owned by the audio thread for `device_callbacks_total`, `device_underrun_count`, `ring_overflow_count`, `samples_consumed_total`; tokio-side `update_snapshot` reads them with `Ordering::Relaxed`. Per Q3, underrun is detected by `consumer.slots() == 0` at the top of the cpal callback. Add `PlaybackSessionInfo` and `PlaybackCodec` to `snapshots.rs`. Wire `data_packets_received` / `_decoded` / `_decode_failed` / `_bytes_received` / `pcm_bytes_produced` into `playback.rs` at the matching code paths (DATA handler is the main one). Wire `start_count` / `stop_count` / `current_session` into the START / STOP handlers. Wire `last_volume_per_channel` / `last_mute` / `last_latency_ms` into the VOLUME / MUTE / LATENCY handlers (currently logged-and-ignored). `recent_decode_durations_us` is a bounded `VecDeque<u32>` capped at 64; push on every successful decode. Extend the playback snapshot serialiser test. **Critical**: snapshot writes on the tokio side must not block the audio callback — verify with `cargo test -p shakenfist-spice-renderer playback` after wiring. Verify `make build && make test && make lint`. |
| 4D | medium | sonnet | none | **UsbredirSnapshot expansion.** Implement the target shape from *Design notes / UsbredirSnapshot fields*. The usbredir channel already tracks server/client caps at handshake time (look at the `usbredir: sending hello` / `server hello` info logs); surface those. For `redirected_devices`: the protocol has `usb_redir_device_connect` (server-to-client) and `usb_redir_device_disconnect` packets. If the existing handler already tracks attached devices, surface the map; if not, add the tracking. Bump `device_connect_total` / `device_disconnect_total` on each event; set `last_device_event_ts_secs` on either. Baseline additions per 4B pattern. Verify `make build && make test && make lint`. |
| 4E | low | sonnet | none | **WebdavSnapshot expansion.** Implement the target shape from *Design notes / WebdavSnapshot fields*. The webdav channel today serves HTTP requests over the spice-vmc transport; bump `http_requests_received` on every parsed request and `http_response_bytes_sent` on each response body. `active_session_count` tracks open HTTP connection objects. `decompressed_size_limit_exceeded_count` is the existing limit-violation path that today only warns. Baseline additions per 4B pattern. Verify `make build && make test && make lint`. |
| 4F | low | haiku | none | **Bug-report writer + docs.** Confirm the new fields land in `channel-state.json` for each channel-specific report type via `ChannelSnapshots::snapshot_json_for`. If `ryll/src/bugreport.rs` has channel-type-specific Connection-report logic that needs to include the new fields, add the wiring (Connection reports only carry main today; consider whether they should also include playback/usbredir if a `record` channel was implicated — defer this UI decision to the operator). Update `AGENTS.md` if the snapshot schema is documented there. Update `docs/troubleshooting.md` with a paragraph on how to interpret the new playback counters (especially `device_underrun_count` and `data_packets_received` going to zero mid-session). Run `pre-commit run --all-files`. |
| 4G | — | — | — | **Operator smoke test (post-phase).** Run a fresh ryll session against `sf-4` (the test target), play a long video (≥60 s) that exercises the audio-during-video symptom from sessions 002d/002e. File a Connection or Playback-typed bug report (whichever exposes `PlaybackSnapshot` — check 4F output). Confirm: (a) `data_packets_received` and `data_packets_decoded` are non-zero and roughly equal during the video session; (b) `device_callbacks_total` increments steadily (proves cpal is pulling); (c) `device_underrun_count` and `ring_overflow_count` together tell us whether the silence is "no samples decoded" or "samples decoded but device starved" or "samples produced too fast." This is operator verification, not a code change. |

Commits: one per step (4A through 4F). 4G is operator verification.

## Test plan

Automated (added by 4A–4F):

- Audit doc lint-clean (4A).
- `messages_*_by_opcode` and unknown-opcode counters increment
  correctly in unit tests for inputs / cursor / main message
  dispatch (4B).
- `PlaybackSnapshot` round-trips through serde with all new
  fields populated; atomic-counter pattern verified by a unit
  test that runs the audio thread skeleton against a mock
  cpal device (4C). If mocking cpal is hard, fall back to a
  lighter test that just exercises the snapshot read path.
- `UsbredirSnapshot` / `WebdavSnapshot` serialiser tests
  extended (4D, 4E).
- `test_display_snapshot_serialises`-style assertions in
  `ryll/src/bugreport.rs` for every new field on every updated
  channel snapshot (4F).

Manual (4G):

- Long-video playback session, Connection report filed,
  fields cross-checked against the symptom. Documented in a
  short comment thread back to the master plan's *Bugs fixed
  during this work* section if anything is found.

## Documentation impact

- `docs/channel-diagnostics-audit.md` (new, from 4A).
- `docs/troubleshooting.md` updated in 4F with a "Playback
  channel observability" section explaining what each new
  counter means and what value ranges to expect.
- `AGENTS.md` updated in 4F only if it currently lists the
  snapshot schema fields.
- Phase 10 (the documentation phase) will further consolidate
  these docs alongside the other stream-caps-and-flap
  changes.

## Success criteria

- An operator can file a single bug report mid-session and
  answer "is the audio actually playing?" by reading the JSON.
  Specifically:
  - `playback.data_packets_received` non-zero in a video
    session → server is sending audio.
  - `playback.data_packets_decoded` non-zero → we're
    decoding it.
  - `playback.device_callbacks_total` increasing →
    CoreAudio / WASAPI / ALSA is pulling.
  - `playback.device_underrun_count` non-zero →
    decode-pipeline can't keep up with the device clock.
- `usbredir.redirected_devices` lists the devices currently
  forwarded to the guest, with bytes-by-direction so
  operators can confirm a redirect is actually flowing data.
- `webdav.http_requests_received` non-zero → the spice-vmc
  webdav bridge is being exercised.
- `docs/channel-diagnostics-audit.md` exists and is linked
  from `docs/index.md`.
- `make build && make test && make lint && pre-commit run
  --all-files` clean.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the operator with their
understanding of the step, the files they intend to touch,
and any deviations from the brief. The audio-thread atomic
counter pattern in step 4C is the highest-risk piece — flag
explicitly if the proposed lock-free approach in Q2 doesn't
fit how cpal callbacks are scoped today.
