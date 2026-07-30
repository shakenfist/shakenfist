# Channel diagnostics audit

## Why this doc exists

Every active SPICE channel should surface enough state in a bug report
that an operator can characterise its current behaviour without
re-running the session with extra logging. This document records the
per-channel observability audit as a living checklist: when a new
channel is added, a new section should be added here before the channel
lands. It was spun out of phase 4 of the
[stream-caps-and-flap](/components/ryll/plans/PLAN-stream-caps-and-flap/) work,
driven by sessions 002d/002e where audio went silent during video
playback and the bug report contained no packet counts, no decoder
errors, and no device-side stats to distinguish between competing
hypotheses. Phase 4 is now complete (verified in session 002g), and
every gap identified in the original audit has been closed; the
sections below describe the shipped state.

## Current audit matrix

The table below reflects the state after phase 4 landed (last
refreshed 2026-07-30). Field counts are the number of public fields on
the channel's snapshot struct in
`shakenfist-spice-renderer/src/snapshots.rs`. Update the **Fields**
and **Notes** columns when a channel's snapshot changes.

| Channel | Fields | Channel-specific signals | Notes |
|---|---|---|---|
| display | 55 | rich: per-stream counters, decoder backends, decode rings, STREAM_REPORT, destroyed-streams ring | no per-opcode maps — deliberate, see below |
| main | 24 | mm_time, session_id, keepalive_timeout_fired | complete |
| inputs | 18 | motion_count, recent_events, KEY_MODIFIERS restatement counters | complete |
| cursor | 18 | cache_entries, cache_contents | complete |
| playback | 28 | full session / decode / device pipeline | complete (phase 4C) |
| usbredir | 18 | caps handshake, redirected-device list, connect/disconnect totals | complete (phase 4D) |
| webdav | 18 | HTTP request/response counters | complete (phase 4E) |
| record | 0 | channel skipped at link time | intentional — see below |

## Minimum diagnostic baseline

Every channel snapshot publishes the transport common eight fields
plus the four baseline additions that landed in phases 4B–4E. The one
exception is display, which carries the transport common fields but
not the per-opcode maps — phase 4 judged its per-stream
instrumentation sufficient (see the display section below). The
baseline fields are declared inline per snapshot struct rather than
via a shared `#[serde(flatten)]`'d struct — less DRY but avoids the
serde-flatten footgun and is consistent with the rest of the file.

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

### Baseline additions (landed in phases 4B–4E)

Phase 4B added these four fields to inputs, cursor, and main; phases
4C–4E added them to playback, usbredir, and webdav alongside the
channel-specific fields described below.

| Field | Description |
|---|---|
| `messages_recv_by_opcode` | Map of server-opcode → receive count since session start; gives a complete picture of what message types the server has sent on this channel |
| `messages_send_by_opcode` | Map of client-opcode → send count since session start |
| `last_unknown_opcode` | The most recent opcode the channel received but did not recognise; surfaces protocol-coverage gaps that `warn_once` would otherwise swallow silently |
| `unknown_opcode_count` | Total number of unrecognised opcodes received since session start |

The `recent_action_ring` field (a bounded ring of structured "interesting
event" rows) was discussed as a fifth baseline addition but is deferred
— display's `recent_decodes` is the only precedent and promoting it to
every channel adds complexity without a clear immediate payoff. Revisit
if a future channel needs a time-ordered event log.

Every snapshot has a serialiser test in `ryll/src/bugreport.rs`
(`test_<channel>_snapshot_serialises`) that constructs a fully
populated snapshot, serialises it to JSON, and asserts each field is
present — catching serde renames before they reach a real bug report.

## Per-channel state

### display

The display channel carries rich channel-specific instrumentation:
per-stream packet/decode/frame counters, the active video decoder
backend (exposed as `video_decoder_backend` per stream, e.g.
"MJPEG (ImageIO)" or "H264 (openh264)"), recent decode-duration
rings, STREAM_REPORT fields, and a `streams_recently_destroyed` ring
that feeds the phase-7 flap notifications. Phase 6 added H.264 support
and the per-stream decoder backend identification.

Display is the one channel without the four per-opcode baseline
fields. Phase 4 judged that its per-stream counters already answer the
"what did the server send?" question in more useful terms than a flat
opcode map would, and no additions were made. Revisit if an
unknown-opcode question ever arises on display.

### main

Main has session identity (`session_id`), multimedia clock state
(`mm_time`, `mm_time_recv_ts_secs`), keepalive-timeout bookkeeping
(`keepalive_timeout_fired`), and the four baseline fields (added in
phase 4B). The `client_keepalive_send_count` and
`last_client_keepalive_send_ts_secs` fields described in earlier
revisions of this audit were removed in phase 11A — the main-channel
spurious-PONG keepalive they tracked was a K1 band-aid made redundant
by the `370d8ce5` fix. (The inputs channel retains the same field
names — see the "inputs" section below — to track its KEY_MODIFIERS
idle restatement, kept on the cross-channel-idleness hypothesis;
see PLAN-stream-caps-and-flap-phase-11-remove-pong-keepalive.md.)

One nuance: the per-opcode map is less useful on main than on other
channels because the agent-related opcodes (`VD_AGENT_*`) are nested
under main's payload framing — a flat opcode map at the main-channel
level won't distinguish agent message subtypes. This is acceptable;
agent observability is a separate concern covered by the phase-8
vdagent probe.

### inputs

Inputs has `motion_count`, a `recent_events` ring that records the
last N input events with timestamps, the KEY_MODIFIERS idle
restatement counters (`client_keepalive_send_count`,
`last_client_keepalive_send_ts_secs` — see the main section above for
why these survive phase 11A), and the four baseline fields (added in
phase 4B). No channel-specific gaps identified.

### cursor

Cursor has `cache_entries` (current number of cached cursors),
`cache_contents` (a summary of what is cached), and the four baseline
fields (added in phase 4B). No channel-specific gaps identified.

### playback

Playback gained its channel-specific fields in phase 4C, closing the
gap that motivated this audit: before, an operator reading a playback
snapshot could not tell whether the server sent any audio data,
whether the client decoded it, or whether the audio device actually
consumed it. The snapshot now surfaces:

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
did the device pull it?" — the three questions the 002d/002e
audio-silence bug required. Phase 4G verified the instrumentation
against a live session (002g) and confirmed it distinguishes the four
failure modes as designed.

### usbredir

Usbredir gained its channel-specific fields in phase 4D:

**Handshake:**
- `server_caps` / `client_caps` — the capability bitmaps observed and
  sent during the usbredir hello exchange

**Device tracking:**
- `redirected_devices` — the list of currently-redirected devices;
  each entry carries the USB vendor ID, product ID, device class,
  the time the redirect was established, and bytes transferred in
  each direction (`bytes_to_guest` / `bytes_from_guest`)
- `device_connect_total` / `device_disconnect_total` — cumulative
  device connect/disconnect events since channel open
- `last_device_event_ts_secs` — timestamp of the most recent device
  connect or disconnect event

### webdav

Webdav gained its channel-specific fields in phase 4E:

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
generic skip arm in `session.rs`'s channel dispatch (the handler logs
`Skipping channel: record`). There is no user-visible surface for
microphone capture today and no `RecordSnapshot` struct. This is
intentional — documenting the skip here was the phase-4 deliverable
for this channel. If microphone capture is added in a future phase, a
`RecordSnapshot` should be created at that point following the
baseline template in this document.

## How to add a new channel

1. **Define a snapshot struct** in
   `shakenfist-spice-renderer/src/snapshots.rs`. Start with the eight
   transport common fields (copy from any existing snapshot) plus the
   four baseline additions (`messages_recv_by_opcode`,
   `messages_send_by_opcode`, `last_unknown_opcode`,
   `unknown_opcode_count`). Add channel-specific fields below a
   separating comment.
2. **Wire opcode counting** in the channel's message-dispatch `match`
   arm: increment `messages_recv_by_opcode[opcode]` on every receive
   and `messages_send_by_opcode[opcode]` on every send. Treat
   unrecognised opcodes as `unknown_opcode_count += 1` /
   `last_unknown_opcode = Some(opcode)` rather than silently ignoring
   them.
3. **Add a `ChannelSnapshots` arm** for the new snapshot type in
   `shakenfist-spice-renderer/src/snapshots.rs`.
4. **Extend `snapshot_json_for`** in `ryll/src/bugreport.rs` so the
   new channel's snapshot is included in the appropriate bug-report
   channel type.
5. **Write a serialiser test** mirroring the
   `test_display_snapshot_serialises` pattern: construct a snapshot
   with all fields populated, serialise to JSON, and assert that each
   field is present with the expected value. This catches serde
   renames and flatten footguns before they reach a real bug report.
6. **Update this document**: refresh the audit matrix and add or
   update the channel's section under *Per-channel state* with a
   brief description of what it surfaces and what gaps (if any)
   remain.
