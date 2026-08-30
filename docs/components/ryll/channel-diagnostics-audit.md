# Channel diagnostics audit

## Why this doc exists

Every active SPICE channel should surface enough state in a bug report
that an operator can characterise its current behaviour without
re-running the session with extra logging. This document records the
per-channel observability audit as a living checklist: when a new
channel is added, a new section should be added here before the channel
lands. The audit was originally motivated by an audio-silence bug whose
bug report contained no packet counts, no decoder errors, and no
device-side stats to distinguish between competing hypotheses (history
in [PLAN-stream-caps-and-flap](/components/ryll/plans/PLAN-stream-caps-and-flap/)).

## Current audit matrix

Field counts are the number of public fields on the channel's snapshot
struct in `shakenfist-spice-renderer/src/snapshots.rs` (last verified
2026-07-31). Update the **Fields** and **Notes** columns when a
channel's snapshot changes.

| Channel | Fields | Channel-specific signals | Notes |
|---|---|---|---|
| display | 55 | rich: per-stream counters, decoder backends, decode rings, STREAM_REPORT, destroyed-streams ring | no per-opcode maps — deliberate, see below |
| main | 24 | mm_time tracking, session_id, keepalive_timeout_fired, agent request/reply stats | complete |
| inputs | 18 | motion_count, recent_events, KEY_MODIFIERS restatement counters | complete |
| cursor | 18 | cache_entries, cache_contents, ack-window state | complete |
| playback | 28 | full session / decode / device pipeline | complete |
| usbredir | 18 | caps handshake, redirected-device list, connect/disconnect totals | complete |
| webdav | 18 | HTTP request/response counters | complete |
| record | 0 | channel skipped at link time | intentional — see below |

## Minimum diagnostic baseline

Every channel snapshot publishes the eight transport common fields
plus the four per-opcode baseline fields. The one exception is
display, which carries the transport common fields but not the
per-opcode maps — its per-stream instrumentation was judged sufficient
(see the display section below). The baseline fields are declared
inline per snapshot struct rather than via a shared
`#[serde(flatten)]`'d struct — less DRY but avoids the serde-flatten
footgun and is consistent with the rest of the file.

### Transport common (universal)

| Field | Description |
|---|---|
| `bytes_in` | Total bytes received on this channel since session start |
| `bytes_out` | Total bytes sent on this channel since session start |
| `last_recv_ts_secs` | Wall-clock timestamp (seconds) of the most recent received message; None if nothing received yet |
| `last_send_ts_secs` | Wall-clock timestamp (seconds) of the most recent sent message; None if nothing sent yet |
| `ping_recv_count` | Number of PING messages received from the server |
| `pong_send_count` | Number of PONG replies sent to the server |
| `last_ping_recv_ts_secs` | Timestamp of the most recent PING received; None if no PING received yet |
| `writer_dropped_count` | Number of outbound writes dropped because the write queue was full |

### Per-opcode baseline (all channels)

| Field | Description |
|---|---|
| `messages_recv_by_opcode` | Map of server-opcode → receive count since session start; gives a complete picture of what message types the server has sent on this channel |
| `messages_send_by_opcode` | Map of client-opcode → send count since session start |
| `last_unknown_opcode` | The most recent opcode the channel received but did not recognise; surfaces protocol-coverage gaps that `warn_once` would otherwise swallow silently |
| `unknown_opcode_count` | Total number of unrecognised opcodes received since session start |

All four are produced by the shared `OpcodeCounters` type in
`shakenfist-spice-renderer/src/opcode_counters.rs`, which every
channel owns one of. Two properties of that type matter when reading
a report:

- **Only named opcodes get a map entry.** The receive opcode is a
  server-chosen `u16`, so an unbounded map is a denial-of-service
  primitive: ~65 000 header-only messages (about 400 KB, sent once)
  would leave every later snapshot publish cloning a 65 000-node map
  under the snapshot mutex. An opcode the protocol crate's
  `message_names` table has no name for therefore folds into
  `last_unknown_opcode` / `unknown_opcode_count` instead of growing
  `messages_recv_by_opcode`.
- **`unknown_opcode_count` covers two cases.** An opcode with no
  protocol name at all, and a named opcode that reached the
  handler's catch-all arm (a real coverage gap). Each message is
  counted once, never both.

A `recent_action_ring` field (a bounded ring of structured
"interesting event" rows) was discussed as a fifth baseline addition
but is deferred — display's `recent_decodes` is the only precedent and
promoting it to every channel adds complexity without a clear
immediate payoff. Revisit if a future channel needs a time-ordered
event log.

Every snapshot has a serialiser test in `ryll/src/bugreport.rs`
(`test_<channel>_snapshot_serialises`) that constructs a fully
populated snapshot, serialises it to JSON, and asserts each field is
present — catching serde renames before they reach a real bug report.

## Per-channel state

### display

The display channel carries rich channel-specific instrumentation:
per-stream packet/decode/frame counters, the active video decoder
backend per stream (`video_decoder_backend`, e.g. "MJPEG (ImageIO)"
or "H264 (openh264)"; the older `mjpeg_decoder_backend` field is
retained for backwards compatibility with bug-report consumers that
key on it), recent decode-duration rings, STREAM_REPORT fields, and a
`streams_recently_destroyed` ring that feeds the stream-flap
notifications.

Display carries the four per-opcode baseline fields like every other
channel. It also carries two stream-safety counters:

- `streams_rejected_total` — `STREAM_CREATE` messages refused
  because the concurrent-stream cap (`MAX_CONCURRENT_STREAMS`, 16)
  was already reached. `stream_id` is a server-chosen `u32` and each
  open stream owns a video decoder — an H.264 decoder costs
  megabytes — so an uncapped map is a memory-exhaustion primitive. A
  non-zero value means the server asked for more concurrent streams
  than the client will carry decoders for.
- A re-`STREAM_CREATE` on a stream id that is still open retires the
  previous stream through the normal teardown path, so it appears in
  `streams_recently_destroyed` and is counted in
  `streams_destroyed_total`. It is exempt from the cap, since
  replacing an entry cannot grow the map, and the replacement
  decoder is built *before* the teardown: a re-create naming a codec
  this build cannot decode is ignored outright rather than
  destroying the working stream and failing to replace it.

`image_cache_ids` is the 64 most recently used cache keys, MRU
first — not the full key set. The cache can hold millions of entries
and the snapshot is republished on every send; `image_cache_entries`
carries the true total.

### main

Main has session identity (`session_id`), multimedia clock state
(`mm_time_now`, `mm_time_set_count`, `last_mm_time_set_ts_secs` — a
frozen clock with a non-advancing set count points at the server
having stopped sending `MULTI_MEDIA_TIME` ticks),
keepalive-timeout bookkeeping (`keepalive_timeout_fired`), agent
round-trip stats (see below), and the four per-opcode baseline
fields.

The agent round-trip stats track vdagent-mediated requests that
expect a `VD_AGENT_REPLY` (today: `VD_AGENT_MONITORS_CONFIG`):
`agent_request_count`, `agent_reply_count`,
`agent_reply_error_count` (replies with a non-zero error field),
`last_agent_reply_ts_secs`, `last_agent_reply_lag_us`, a bounded
`recent_agent_reply_lag_us` ring, and
`outstanding_agent_request_count` (persistently > 0 means the agent
is wedged).

Main deliberately has no client-keepalive send counters: the
spurious-PONG keepalive they once tracked was a band-aid made
redundant when the underlying main-channel wedge was fixed
(`370d8ce5`). The inputs channel carries identically-named counters
for a different mechanism — see the inputs section below.

One nuance: the per-opcode map is less useful on main than on other
channels because the agent-related opcodes (`VD_AGENT_*`) are nested
under main's payload framing — a flat opcode map at the main-channel
level won't distinguish agent message subtypes. The agent round-trip
stats above cover the request/reply cycle; deeper agent traffic
inspection is the vdagent probe's job.

### inputs

Inputs has `motion_count`, `button_state`, `secs_since_last_key`, a
`recent_events` ring that records the last N input events with
timestamps, the KEY_MODIFIERS idle restatement counters
(`client_keepalive_send_count`, `last_client_keepalive_send_ts_secs`
— these count the periodic KEY_MODIFIERS restatement kept on the
cross-channel-idleness hypothesis, and are unrelated to the removed
main-channel keepalive despite the shared names), and the four
per-opcode baseline fields. No channel-specific gaps identified.

### cursor

Cursor has `cache_entries` (current number of cached cursors),
`cache_contents` (a summary of what is cached), ack-window state
(`ack_generation`, `ack_window`, `message_count`, `last_ack`), and
the four per-opcode baseline fields. No channel-specific gaps
identified.

### playback

Playback's channel-specific fields close the gap that motivated this
audit: without them, an operator reading a playback snapshot could
not tell whether the server sent any audio data, whether the client
decoded it, or whether the audio device actually consumed it. The
snapshot surfaces:

**Session metadata:**
- `current_session` — populated while a playback session is active:
  when it started (`started_at_secs`), the multimedia clock value the
  server assigned at start (`mm_time_at_start`), and the negotiated
  `sample_rate_hz`, `channels`, and `codec` (Opus, raw PCM, or an
  unrecognised codec number)
- `start_count` / `stop_count` — cumulative `PLAYBACK_START` /
  `PLAYBACK_STOP` messages received since session open
- `last_volume_per_channel`, `last_mute`, `last_latency_ms` — the
  most recent volume, mute, and latency-hint values set by the server

**Data plumbing:**
- `data_packets_received` / `data_packets_decoded` /
  `data_packets_decode_failed` — `PLAYBACK_DATA` packet counters
- `data_bytes_received` — total compressed bytes received
- `pcm_bytes_produced` — total PCM bytes produced by the decoder
- `recent_decode_durations_us` — a recent ring of per-packet decode
  durations in microseconds, for spotting decode-latency spikes

**Device pipeline (fed from audio-thread atomics):**
- `device_callbacks_total` — cpal output-callback invocations since
  the most recent device open (proves the audio device is pulling)
- `device_underrun_count` — callbacks where the ring buffer had zero
  ready samples at entry: true underruns where we handed the device
  silence
- `ring_overflow_count` — decoded samples dropped because the ring
  buffer was full (decoder running ahead of the device clock)
- `samples_consumed_total` — samples consumed by the device since the
  most recent device open

Together these answer: "did the server send audio? did we decode it?
did the device pull it?" — the three questions an audio-silence bug
report needs, verified against a live session to confirm the
instrumentation distinguishes the failure modes as designed.

### usbredir

**Handshake:**
- `server_caps` / `client_caps` — the capability bitmaps observed and
  sent during the usbredir hello exchange

**Device tracking:**
- `redirected_devices` — the list of currently-redirected devices;
  each entry carries the USB vendor ID, product ID, device class,
  the time the redirect was established (`attached_at_secs`), and
  bytes transferred in each direction (`bytes_to_guest` /
  `bytes_from_guest`). The byte counters cover usbredir-protocol
  bytes — framed usbredir messages outbound, decompressed SPICEVMC
  payload inbound — and reset on each attachment, so they are not
  comparable with the channel-wide `bytes_in` / `bytes_out`
- `device_connect_total` / `device_disconnect_total` — cumulative
  device connect/disconnect events since channel open
- `last_device_event_ts_secs` — timestamp of the most recent device
  connect or disconnect event

### webdav

- `http_requests_received` — HTTP requests received over the
  spice-vmc transport
- `http_response_bytes_sent` — total bytes sent in HTTP response
  bodies
- `active_session_count` — currently-open HTTP connection objects
- `last_request_ts_secs` / `last_response_ts_secs` — timestamps of
  the most recent HTTP request received and response sent
- `decompressed_size_limit_exceeded_count` — times the
  decompressed-size limit was exceeded (a warn-only path; the count
  lets operators know the limit is being hit)

### record

The record channel is not established: it falls through to the
generic skip arm in `shakenfist-spice-renderer/src/session.rs`'s
channel dispatch (the handler logs `Skipping channel: record`). There
is no user-visible surface for microphone capture today and no
`RecordSnapshot` struct. This is intentional. If microphone capture
is added in the future, a `RecordSnapshot` should be created at that
point following the baseline template in this document.

## How to add a new channel

1. **Define a snapshot struct** in
   `shakenfist-spice-renderer/src/snapshots.rs`. Start with the eight
   transport common fields (copy from any existing snapshot) plus the
   four per-opcode baseline fields (`messages_recv_by_opcode`,
   `messages_send_by_opcode`, `last_unknown_opcode`,
   `unknown_opcode_count`). Add channel-specific fields below a
   separating comment.
2. **Wire opcode counting** by giving the channel an
   `OpcodeCounters` field, constructed with that channel's
   `message_names` server and client functions. Call `record_recv`
   before dispatch, `record_send` from the single send path,
   `note_unknown` from the catch-all `match` arm, and
   `publish_into(&mut *snap)` from `update_snapshot`. Do not
   hand-roll the four fields: the counters must stay bounded, and
   `OpcodeCounters` is where that bound lives.
3. **Extend `ChannelSnapshots`** in
   `shakenfist-spice-renderer/src/snapshots.rs`: add a field for the
   new snapshot type, initialise it in `new()`, and add arms in
   `snapshot_json_for` (both the single-channel match and the `"all"`
   merge used by auto-snapshot reports).
4. **Wire the bug-report side** in `ryll/src/bugreport.rs` so the
   appropriate report type's `channel_name()` resolves to the new
   channel and its snapshot lands in the report's channel-state JSON.
5. **Write a serialiser test** mirroring the
   `test_display_snapshot_serialises` pattern: construct a snapshot
   with all fields populated, serialise to JSON, and assert that each
   field is present with the expected value. This catches serde
   renames and flatten footguns before they reach a real bug report.
6. **Update this document**: refresh the audit matrix and add or
   update the channel's section under *Per-channel state* with a
   brief description of what it surfaces and what gaps (if any)
   remain.
