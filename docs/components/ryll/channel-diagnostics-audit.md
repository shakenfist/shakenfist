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
hypotheses.

## Today's audit matrix

The table below is a snapshot of the state at phase-4 plan-write time
(2026-05-18). Update the **Fields** and **Gap** columns when a channel's
snapshot is expanded.

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

## Minimum diagnostic baseline

Every channel snapshot should publish the transport common eight fields
(already universal across all channels) plus four new baseline additions.
The baseline additions are added inline per snapshot struct rather than
via a shared `#[serde(flatten)]`'d struct — less DRY but avoids the
serde-flatten footgun and is consistent with today's pattern.

### Transport common (already universal)

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

### New baseline additions (phases 4B–4E)

These four fields should be added to every channel snapshot that does
not already have them. Phase 4B adds them to inputs, cursor, and main;
phases 4C–4E add them to playback, usbredir, and webdav alongside
channel-specific fields.

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

## Per-channel wish-lists

### display

The display channel already meets the baseline and, post phase 3, carries
rich channel-specific instrumentation: per-stream packet/decode/frame
counters, the active video decoder backend (exposed as `video_decoder_backend`
per stream, e.g. "MJPEG (ImageIO)" or "H264 (openh264)"), recent decode-duration
rings, STREAM_REPORT fields, and a `streams_recently_destroyed` ring that feeds
the phase-7 flap-notification work. Phase 6 adds H.264 support and per-stream
decoder backend identification. No additions needed in phase 4.

### main

Main already has session identity (`session_id`), multimedia clock state
(`mm_time`, `mm_time_recv_ts_secs`), and keepalive-timeout bookkeeping
(`keepalive_timeout_fired`). The `client_keepalive_send_count` and
`last_client_keepalive_send_ts_secs` fields described in earlier
revisions of this audit were removed in phase 11A — the main-channel
spurious-PONG keepalive they tracked was a K1 band-aid made redundant
by the `370d8ce5` fix. (The inputs channel retains the same field
names — see the "inputs" section above — to track its KEY_MODIFIERS
idle restatement, kept on the cross-channel-idleness hypothesis;
see PLAN-stream-caps-and-flap-phase-11-remove-pong-keepalive.md.) The
four new baseline fields (`messages_recv_by_opcode` etc.) will be
added in phase 4B.

One nuance: the per-opcode map is less useful on main than on other
channels because the agent-related opcodes (`VD_AGENT_*`) are nested
under main's payload framing — a flat opcode map at the main-channel
level won't distinguish agent message subtypes. This is acceptable for
now; agent observability is a separate concern covered by the vdagent
probe in phase 8.

### inputs

Inputs already has `motion_count` and a `recent_events` ring that
records the last N input events with timestamps. The four new baseline
fields will be added in phase 4B. No channel-specific gaps identified.

### cursor

Cursor already has `cache_entries` (current number of cached cursors)
and `cache_contents` (a summary of what is cached). The four new
baseline fields will be added in phase 4B. No channel-specific gaps
identified.

### playback

Playback today has only the eight transport common fields. An operator
reading a playback snapshot cannot tell whether the server sent any
audio data, whether the client decoded it, or whether the audio device
actually consumed it. The target shape after phase 4C should include:

**Session metadata:**
- Whether a playback session is currently active and, if so, when it
  started and what multimedia clock value the server assigned at start
- Cumulative count of `PLAYBACK_START` messages received since session
  open (each start begins a new audio session)
- Cumulative count of `PLAYBACK_STOP` messages received
- The codec in use for the current session (Opus, raw PCM, or an
  unrecognised codec number)
- The sample rate and channel count negotiated at the most recent start
- The last volume levels per channel and the last mute flag, as set
  by the server
- The last latency hint the server sent (in milliseconds)

**Data plumbing:**
- Count of `PLAYBACK_DATA` packets received from the server
- Count of those packets successfully decoded
- Count of those packets that failed to decode
- Total compressed bytes received (sum of DATA payload lengths)
- Total PCM bytes produced by the decoder
- A recent ring of per-packet decode durations in microseconds
  (capacity ~64; useful for spotting decode-latency spikes)

**Device pipeline (fed from audio-thread atomics):**
- Total cpal output-callback invocations since the most recent
  device open (proves the audio device is pulling)
- Count of callbacks where the ring buffer had zero ready samples
  at callback entry — these are true underruns where we handed
  the device silence
- Count of times decoded samples were dropped because the ring
  buffer was full (encoder running ahead of the device clock)
- Total samples consumed by the device since the most recent
  device open

These counters together let an operator answer: "did the server send
audio? did we decode it? did the device pull it?" — the three
questions the 002d/002e audio-silence bug required.

### usbredir

Usbredir today has only the eight transport common fields. A bug report
for a USB redirect problem gives no information about which devices are
currently forwarded or whether any data is flowing. The target shape
after phase 4D should include:

**Handshake:**
- The server capability bitmap observed during the usbredir hello
  exchange
- The client capability bitmap we sent

**Device tracking:**
- The list of currently-redirected devices; for each device: USB
  vendor ID, product ID, device class, time the redirect was
  established, bytes transferred to the guest, and bytes received
  from the guest
- Cumulative count of device-connect events since channel open
- Cumulative count of device-disconnect events since channel open
- Timestamp of the most recent device connect or disconnect event

### webdav

Webdav today has only the eight transport common fields. A bug report
for a guest file-sharing problem gives no information about whether any
HTTP requests have been processed. The target shape after phase 4E
should include:

- Count of HTTP requests received over the spice-vmc transport
- Total bytes sent in HTTP response bodies
- Number of currently-open HTTP connection objects (active sessions)
- Timestamp of the most recent HTTP request received
- Timestamp of the most recent HTTP response sent
- Count of times the decompressed-size limit was exceeded (currently
  a warn-only path; surfacing the count lets operators know the limit
  is being hit)

### record

The record channel is skipped at link time in `main_channel.rs` (the
handler logs `Skipping channel: record` and does not establish the
channel). There is no user-visible surface for microphone capture today
and no `RecordSnapshot` struct. This is explicitly in scope for phase
4 to document rather than implement — the channel stays skipped with
a one-line comment explaining why. If microphone capture is added in
a future phase, a `RecordSnapshot` should be created at that point
following the baseline template in this document.

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
6. **Update this document**: add a new section under
   *Per-channel wish-lists* with a brief description of what the new
   channel surfaces and what gaps (if any) remain.
