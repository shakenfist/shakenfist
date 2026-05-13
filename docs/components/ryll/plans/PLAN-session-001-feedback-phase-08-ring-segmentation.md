# Phase 08: Segment large messages in ring-buffer frame builder

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`ryll/src/capture.rs:52-108` (the **working** live-writer
segmentation: `write_segmented` splits at `MAX_PAYLOAD =
65495 = 65535 − 20 − 20` and per-segment seqs are
`base + offset`), `ryll/src/capture.rs:111-160`
(`build_tcp_frame` returns an empty `Vec<u8>` and warns when
the payload would push the IPv4 frame past 65515 bytes — this
is the failure surface for K2), `ryll/src/bugreport.rs:365-388`
(the **broken** ring-buffer counterpart: `build_frame` calls
`build_tcp_frame` once with the whole payload, so any SPICE
message above ~64 KB lands in the ring as a 0-byte
`pcap_frame`), `ryll/src/bugreport.rs:38-59`
(`TrafficEntry`, now `pcap_frame: Arc<[u8]>` post-Phase-07),
and `ryll/src/bugreport.rs:438-464` (`recent_view_entries`,
which drives the in-app traffic viewer — needs a small filter
once segmentation produces N entries per oversized message).

This phase fixes bug **K2** — "Ring-buffer frame builder
drops SPICE messages > 64 KB" — identified from session-001
pedantic captures N4 / B1 / B2. The master plan classifies
this as a separate phase ("mirror `write_segmented`; pairs
with Phase 07") and explicitly notes the entry-per-segment
shape as the right fit for the `Arc<[u8]>` model Phase 07 just
landed.

## Situation

### What we already established

**The bug is one missing segmentation loop.** `bugreport.rs`'s
`build_frame` is structurally a one-liner around
`capture::build_tcp_frame`, which itself fails closed (returns
an empty Vec) when the IPv4 frame would exceed 65515 bytes.
`capture.rs`'s live writer wraps the same `build_tcp_frame`
in `write_segmented`, which splits the payload into chunks
of `MAX_PAYLOAD = 65495` bytes and produces one TCP frame
per chunk with per-segment seq numbers `base + offset`. The
ring-buffer path skips the loop and therefore drops large
messages.

**SPICE display messages can absolutely exceed 64 KB.** The
display channel sends MJPEG / LZ4 / GLZ-compressed frame
chunks; at session-001's measured 2 MB/s typical (6 MB/s
peak) with frame intervals of tens of milliseconds, individual
messages routinely cross 64 KB on high-resolution or fast-
changing content. Sessions N4 / B1 / B2 captured pedantic
zips with empty `pcap_frame` entries that line up exactly
with display-channel STREAM_DATA / DRAW_COPY messages whose
`wire_size` (header byte 2-5) exceeds 65 KB.

**The live `--capture` path is unaffected.** It already calls
`write_segmented` and produces correctly-segmented pcaps. K2
is strictly about the in-memory ring buffer — which is the
input to F8 bug reports, disconnect snapshots, and (soon)
the Phase 10 notification snapshots.

**Phase 07 just made the type plumbing ready.** With
`pcap_frame: Arc<[u8]>`, splitting one message across
multiple ring entries means N atomic refcount bumps if any
consumer ever wants to clone them — which is exactly what
Phase 10 will do.

### What we don't yet know

Nothing material to the fix itself. Two adjacent design
questions:

1. **Where the segmentation logic lives**: in `bugreport.rs`
   (mirrored, duplicating capture.rs's logic) or in a shared
   helper called by both. Resolved under "Approach" below
   — share via a new `capture::segment_payload` helper.

2. **What the traffic viewer should show for a segmented
   message**: one row covering the whole message, or N rows
   for the segments. Resolved under "Approach" — one row,
   first segment carries the canonical metadata, the viewer
   filters subsequent segments.

## Mission and problem statement

Make the ring-buffer frame builder segment large SPICE
messages the same way the live pcap writer does, so that:

- Display-channel messages above 64 KB no longer produce
  zero-byte `pcap_frame` entries.
- A bug-report pcap drained from the ring shows those
  messages as one or more TCP segments with the correct
  per-segment seq numbers — Wireshark and any pcap-aware
  tool can reassemble them.
- The in-app traffic viewer still shows one row per SPICE
  message (not N rows for an N-segmented message).
- The ring's byte accounting reflects the total bytes
  retained (sum across all segments of an entry).
- Phase 07's cheap-clone property is preserved — each
  segment is its own `Arc<[u8]>`.

The phase succeeds when:

- A bug report captured during a session with sustained
  display traffic includes complete display-channel pcap
  data for messages > 64 KB, verifiable in Wireshark.
- The `recent_view_entries` traffic viewer collapses
  segmented messages back to one row per SPICE message, so
  the user-facing summary is unchanged.
- `TrafficRingBuffer::total_bytes()` correctly sums all
  segments of all entries (i.e. byte caps remain
  enforceable).
- No new dependencies; the segmentation helper is shared
  with `capture.rs` rather than duplicated.

## Approach

### Shared `segment_payload` helper in `capture.rs`

Extract the segmentation core out of `write_segmented` into a
free function next to `build_tcp_frame`:

```rust
/// Split `data` into one or more TCP segments and produce the
/// pcap frame bytes for each. Per-segment seq numbers are
/// `seq + offset`; the caller is responsible for advancing
/// its own stream-wide seq tracking by `data.len()` after
/// this call returns. Always returns at least one frame:
/// callers pass non-empty SPICE messages (the protocol's
/// 6-byte header is always present), but the loop is shaped
/// so a 0-byte input would still produce one zero-length
/// frame rather than an empty Vec — defensive belt against a
/// caller that ever passes an empty slice.
pub(crate) fn segment_payload(
    src_ip: [u8; 4],
    src_port: u16,
    dst_ip: [u8; 4],
    dst_port: u16,
    seq: u32,
    ack: u32,
    data: &[u8],
) -> Vec<Vec<u8>> {
    const MAX_PAYLOAD: usize = 65495;
    let mut frames = Vec::new();
    let mut offset = 0;
    while offset < data.len() {
        let end = (offset + MAX_PAYLOAD).min(data.len());
        let chunk = &data[offset..end];
        let segment_seq = seq.wrapping_add(offset as u32);
        frames.push(build_tcp_frame(
            src_ip, src_port, dst_ip, dst_port, segment_seq, ack, chunk,
        ));
        offset = end;
    }
    if frames.is_empty() {
        // Pathological: empty payload. Produce a single empty
        // frame so the caller's "one entry per call" invariant
        // holds.
        frames.push(build_tcp_frame(
            src_ip, src_port, dst_ip, dst_port, seq, ack, data,
        ));
    }
    frames
}
```

Then `PcapChannelWriter::write_segmented` becomes a thin
wrapper:

```rust
fn write_segmented(
    &mut self,
    src_ip: [u8; 4],
    src_port: u16,
    dst_ip: [u8; 4],
    dst_port: u16,
    data: &[u8],
    elapsed: std::time::Duration,
) {
    let is_client = src_ip == CLIENT_IP;
    let (seq, ack) = if is_client {
        (self.client_seq, self.server_seq)
    } else {
        (self.server_seq, self.client_seq)
    };
    for frame in segment_payload(src_ip, src_port, dst_ip, dst_port, seq, ack, data) {
        self.write_frame(&frame, elapsed);
    }
}
```

Same behaviour, but the segmentation loop now lives in one
place.

### `TrafficEntry` carries optional additional segments

Add a `Vec<Arc<[u8]>>` field:

```rust
pub struct TrafficEntry {
    // ... unchanged ...
    pub pcap_frame: Arc<[u8]>,
    /// Additional TCP segments produced when a SPICE message
    /// exceeds the IPv4 frame limit. Empty in the common case
    /// (one segment fits in `pcap_frame`); a few entries for
    /// larger messages. Each segment is an independent
    /// `Arc<[u8]>` so clones (Phase 10 snapshot path) remain
    /// O(N atomic refcount bumps).
    pub additional_segments: Vec<Arc<[u8]>>,
}
```

Rationale for this shape over "entry-per-segment":

- **Traffic viewer simplicity.** `recent_view_entries`
  already emits one `TrafficViewEntry` per `TrafficEntry`;
  with this shape that's still one row per SPICE message,
  no filter logic needed.
- **Byte-cap eviction is atomic per message.** A 200 KB
  message either fully fits or fully evicts; we never end
  up with a "tail end of a half-evicted message" in the
  ring. The live capture pcap has no such property (it
  writes packets as they happen), but the ring buffer is a
  retention store where atomicity is friendlier to a
  maintainer reading the drained pcap cold.
- **Common case is cheap.** Empty `Vec`s don't allocate;
  `Vec::clone()` of an empty Vec is also non-allocating.
  Phase 07's cheap-clone property holds — for an unsegmented
  message, clone is dominated by the small fixed-size
  fields; for a segmented one, it's N refcount bumps for
  the segments plus a small Vec allocation.

Byte accounting becomes:

```rust
fn entry_bytes(entry: &TrafficEntry) -> usize {
    entry.pcap_frame.len()
        + entry.additional_segments.iter().map(|s| s.len()).sum::<usize>()
}
```

Used in `TrafficRingBuffer::push` (both on insert and on
evict) to maintain `total_bytes` correctly.

### Producer call sites build the segmented entry in one go

`build_frame` in `bugreport.rs` becomes `build_segmented_frames`,
returning `Vec<Arc<[u8]>>` — always non-empty:

```rust
fn build_segmented_frames(
    &self,
    channel: &str,
    is_sent: bool,
    data: &[u8],
    ring: &mut TrafficRingBuffer,
) -> Vec<Arc<[u8]>> {
    let port = capture::channel_port(channel);
    let (src_ip, src_port, dst_ip, dst_port, seq, ack) = if is_sent {
        let s = ring.client_seq;
        let a = ring.server_seq;
        ring.client_seq = ring.client_seq.wrapping_add(data.len() as u32);
        ([10, 0, 0, 1], port, [10, 0, 0, 2], 5900u16, s, a)
    } else {
        let s = ring.server_seq;
        let a = ring.client_seq;
        ring.server_seq = ring.server_seq.wrapping_add(data.len() as u32);
        ([10, 0, 0, 2], 5900u16, [10, 0, 0, 1], port, s, a)
    };
    capture::segment_payload(src_ip, src_port, dst_ip, dst_port, seq, ack, data)
        .into_iter()
        .map(Arc::<[u8]>::from)
        .collect()
}
```

`record_received` / `record_sent` then build the entry from
the segmented frames:

```rust
let mut frames = self.build_segmented_frames(channel, false, raw_message, &mut guard);
let pcap_frame = frames.remove(0);
let additional_segments = frames;
let entry = TrafficEntry { /* ... */, pcap_frame, additional_segments };
```

`Vec::remove(0)` is O(N) but N is small (1 in the common
case, 2-5 for oversized messages) and called once per
recorded message.

### Pcap export iterates segments in order

In `TrafficRingBuffer::write_pcap_to`:

```rust
for entry in &self.entries {
    pcap.write_packet(&PcapPacket::new(
        entry.timestamp,
        entry.pcap_frame.len() as u32,
        &entry.pcap_frame[..],
    )).ok();
    for seg in &entry.additional_segments {
        pcap.write_packet(&PcapPacket::new(
            entry.timestamp,
            seg.len() as u32,
            &seg[..],
        )).ok();
    }
    count += 1;  // count messages, not segments
}
```

`count` reflects messages (matches today's semantics).
Multi-segment messages produce multiple pcap packets at the
same timestamp, which is how the live writer renders them
too — consistent across the two paths.

### Tests

- `bugreport::tests::traffic_entry_segments_large_message` —
  push a >65 KB synthetic message through `record_received`,
  pop the entry, assert `pcap_frame` is the first segment
  (65495 bytes payload) and `additional_segments` contains
  the remaining chunks.
- `bugreport::tests::traffic_entry_segment_seqs_chain_correctly`
  — segment a 200 KB payload directly via
  `segment_payload`, parse the TCP headers out of each frame
  (cheap — the offsets are constant), assert seq numbers
  are `base + 0`, `base + 65495`, `base + 130990`.
- `bugreport::tests::traffic_ring_byte_cap_evicts_segmented_entries`
  — fill a ring with a few segmented entries, exceed the
  cap, assert eviction frees both `pcap_frame` and
  `additional_segments` bytes from `total_bytes`.
- `capture::tests::segment_payload_split_at_max` — basic
  unit test of the shared helper: 130000 bytes → 2 frames
  (65495 + 64505 payload bytes).
- `capture::tests::segment_payload_single_segment_under_max`
  — 1000 bytes → 1 frame with full payload.
- The existing
  `bugreport::tests::traffic_entry_clone_shares_pcap_frame_via_arc`
  is extended (or paired with) a check that
  `additional_segments` is also `Arc::ptr_eq`-shared after
  clone.

### Manual verification

Bundles with the running Phase 02–07 manual checklist:

1. Connect to a guest, play a busy video clip.
2. Hit F8 to fire a manual bug report.
3. Open the resulting pcap in Wireshark; filter to
   display-channel port. Confirm the byte counts on
   STREAM_DATA / DRAW_COPY messages match the reported
   `wire_size`, with no truncated payloads.
4. For messages above 64 KB, confirm Wireshark renders them
   as multiple TCP segments with adjacent sequence numbers
   that reassemble into the full SPICE payload.

## Tasks

- [x] Extracted `segment_payload(src_ip, src_port, dst_ip,
      dst_port, seq, ack, data)` as a `pub(crate)` free
      function in `ryll/src/capture.rs`. Defensive fallback
      for empty data produces one empty frame so callers
      don't have to special-case the return value.
- [x] `PcapChannelWriter::write_segmented` rewritten to call
      `segment_payload` and write each returned frame via
      `write_frame`. Same seq-advance semantics at the
      `write_sent` / `write_received` call sites.
- [x] Added `pub additional_segments: Vec<Arc<[u8]>>` to
      `TrafficEntry`. Doc comment explains the segmentation
      model and the empty-Vec common case (no allocation).
- [x] Replaced `build_frame` with `build_segmented_frames`
      returning `Vec<Arc<[u8]>>`. Both producer sites
      (`record_received`, `record_sent`) build the entry by
      `remove(0)`-ing the first frame as `pcap_frame` and
      using the rest as `additional_segments`.
- [x] Added free function `entry_bytes(entry)` that sums
      `pcap_frame.len() + additional_segments`; used in
      `TrafficRingBuffer::push` for both insertion and
      eviction byte accounting.
- [x] `TrafficRingBuffer::write_pcap_to` updated to iterate
      `pcap_frame` then `additional_segments` per entry,
      writing one pcap packet per segment at the entry's
      timestamp. `count` increments per entry (= per
      message), preserving pre-Phase-08 return-value
      semantics.
- [x] Both test stub literals now include
      `additional_segments: Vec::new()`.
- [x] Extended `traffic_entry_clone_shares_pcap_frame_via_arc`
      to also assert `Arc::ptr_eq` across cloned
      `additional_segments` entries.
- [x] Added the five new tests:
  - `bugreport::tests::traffic_entry_segments_large_message`
  - `bugreport::tests::traffic_ring_byte_cap_evicts_segmented_entries`
  - `capture::tests::segment_payload_split_at_max`
  - `capture::tests::segment_payload_single_segment_under_max`
  - `capture::tests::segment_payload_seqs_chain_correctly`
- [x] Updated `PLAN-session-001-feedback.md` K2 row in the
      "Confirmed bugs" table with the resolution pointer.
- [x] Updated `PLAN-session-001-feedback.md` Execution
      table row for Phase 08 → Done.
- [ ] Manual integration check (deferred operator action,
      bundles with the running Phase 02–07 manual checklist):
      busy-display F8 report → Wireshark verification of
      multi-segment messages with correct seq chaining.

## Out of scope

- **Changing the live capture path's wire format.** The live
  pcap output is already correct; the refactor share via
  `segment_payload` preserves byte-for-byte equivalence on
  that path.
- **Per-segment pcap packet timestamps.** The live writer
  uses the same `elapsed` for all segments of a message,
  which matches the SPICE wire reality (the message arrived
  / departed in one logical event). The ring keeps that
  shape. Sub-segment timing would invent precision the
  source data doesn't have.
- **MTU tuning / Path-MTU discovery.** The 65495 cap is the
  IPv4-frame ceiling, not a network MTU. Real Ethernet MTU
  considerations belong to the SPICE TCP socket layer, not
  to ryll's pcap synthesis.
- **Compressing pcap output.** Pcap-NG with the
  segmented-message option, or gzip on the zip's pcap
  attachment, are separate questions. Phase 08 is just about
  not dropping bytes.
- **Reassembly hints in the SPICE-channel-state JSON.**
  Wireshark already reassembles TCP segments natively given
  correct seq numbers, so no extra hint is needed for
  consumers of the bug-report zip.

## Open questions

1. **Should `additional_segments` be `Option<Box<[Arc<[u8]>]>>`
   instead of `Vec<Arc<[u8]>>` to shave the empty-case Vec
   header off `TrafficEntry`'s footprint?** No — an empty
   `Vec` doesn't allocate, and the 24 bytes of footprint
   per entry is dwarfed by the `pcap_frame` Arc and the
   `&'static str`s. Premature optimisation.

2. **Should the traffic viewer expose the segment count
   anywhere?** A small "(segmented: 3)" annotation on the
   wire-size column would help a maintainer debugging a
   large-message issue, but isn't load-bearing for the K2
   fix. Defer; revisit if session-002 turns up a need.

3. **Should the seq-chain test parse all TCP headers, or
   just spot-check the offset arithmetic?** Spot-check is
   enough — `build_tcp_frame` itself isn't being changed
   here, and ryll's existing tests on capture.rs already
   cover frame structure. The new test pins
   `segment_payload`'s offset math specifically.

## Companion docs

None new. `ARCHITECTURE.md` may have a ring-buffer
description that mentions "one pcap frame per entry" — if
so, it'll need a one-sentence amendment to acknowledge the
multi-segment case. Check during the wrap-up step and edit
if present; if not present, no doc update needed.
`AGENTS.md` has no entry to update — segmentation is an
internal implementation detail.
