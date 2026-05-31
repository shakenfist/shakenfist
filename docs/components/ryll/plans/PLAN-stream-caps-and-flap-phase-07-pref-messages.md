# Phase 7 — Preference messages

Phase 7 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Goal

Advertise SPICE display caps `PREF_COMPRESSION` (bit 6) and
`PREF_VIDEO_CODEC_TYPE` (bit 12), and send the corresponding
preference messages once on link establishment so the server
picks the codec and image-compression scheme ryll prefers.
Mechanical follow-on to phase 6's `MULTI_CODEC` work.

Reference behaviour: spice-gtk's `channel-display.c::display_init`
sends `SPICE_MSGC_DISPLAY_PREFERRED_COMPRESSION` (opcode 103)
and `SPICE_MSGC_DISPLAY_PREFERRED_VIDEO_CODEC_TYPE` (opcode 105 — earlier draft of this doc said 104, which is actually `GL_DRAW_DONE`; 105 confirmed against `/srv/src-reference/spice/spice-protocol/spice/enums.h:523-530` during 7A implementation)
right after the channel reaches the `STATE_LINKED` state.

## Scope

In scope:

- New constants in `shakenfist-spice-protocol/src/constants.rs`:
  `SPICE_DISPLAY_CAP_PREF_COMPRESSION = 6`,
  `SPICE_DISPLAY_CAP_PREF_VIDEO_CODEC_TYPE = 12`,
  `SPICE_MSGC_DISPLAY_PREFERRED_COMPRESSION = 103`,
  `SPICE_MSGC_DISPLAY_PREFERRED_VIDEO_CODEC_TYPE = 105` (confirm
  values against `spice-protocol/spice/messages.h`).
- Add the two cap bits to the display channel's advertised cap
  set (same site that adds 8/9/11 from phase 6).
- Wire two message senders into the display channel's link-up
  path (`shakenfist-spice-renderer/src/channels/display.rs`),
  fired once when the channel transitions to "linked".
- Counters on `DisplaySnapshot`: `pref_compression_sent: bool`,
  `pref_video_codec_type_sent: bool`. Cheap to add; tells a
  bug-report reader whether the preferences actually went out.

Out of scope:

- Configuring which preferences. Hardcode for v1:
  `PREFERRED_COMPRESSION = SPICE_IMAGE_COMPRESSION_AUTO_GLZ`
  (see "Session 006 follow-up" below) and
  `PREFERRED_VIDEO_CODEC_TYPE = [SPICE_VIDEO_CODEC_TYPE_H264,
  SPICE_VIDEO_CODEC_TYPE_MJPEG]` (H.264 preferred, MJPEG
  fallback — matches phase 6's intent). A CLI flag for these is
  a phase-10 nicety, not v1.

## Session 006 follow-up — AUTO_LZ → AUTO_GLZ

Initial v1 advertised `AUTO_LZ`. Session 006 measurement (006a
pre-phase-7 vs 006c post-phase-7, same UI-heavy workload):

| Tag | glz_dict_entries | glz_evictions | bytes_in (10 min) |
|-----|------------------|---------------|--------------------|
| 006a (pre-phase-7, server default) | 23 | 1345 | 2.78 GB |
| 006c (post-phase-7, AUTO_LZ)       | **0**  | **0**    | **3.51 GB** (+25%) |

Advertising `AUTO_LZ` made the server stop using GLZ entirely.
For a UI-heavy workload (desktop chrome, taskbar) repeats
across frames are common and GLZ's shared dictionary is the
right encoder. `AUTO_GLZ` still lets the server pick QUIC for
photographic content but keeps the dictionary for repeating
UI elements.

Switched to `image_compression::AUTO_GLZ` (value 2) at
`shakenfist-spice-renderer/src/channels/display.rs:853`.
Pinning test renamed
`image_compression_auto_lz_value_pinned` →
`image_compression_auto_glz_value_pinned`. Re-measure on the
next 006-style run to confirm GLZ dictionary use returns and
bytes_in approaches 006a's baseline.
- Negotiating dynamically with the server. The preferences are
  one-shot link-up messages; no retry or re-send.
- Adding equivalent caps/messages for non-display channels.
  None today.

## Step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7A | medium | sonnet | none | **Constants + cap advertisement.** Add the four new constants in `shakenfist-spice-protocol/src/constants.rs`. Confirm exact numeric values against `/srv/src-reference/spice/spice-protocol/spice/messages.h` (do NOT guess — read the header). Add bits 6 and 12 to the display channel's cap advertisement at the same site phase 6 added 8/9/11. Unit tests for constant values. |
| 7B | medium | sonnet | none | **Wire the two send paths.** In `shakenfist-spice-renderer/src/channels/display.rs`, find the link-up state transition (where ATTACH_CHANNELS / INIT is handled). After STATE_LINKED, send `SpiceMsgcDisplayPreferredCompression { image_compression: AUTO_LZ as u8 }` and `SpiceMsgcDisplayPreferredVideoCodecType { num_codecs: 2, codecs: [H264, MJPEG] }`. Read spice-gtk's `display_init` and its `prefer_compression` / `prefer_video_codec_type` helpers for the exact wire format and codec-list encoding (it's a length-prefixed array of u8 codec types). One-shot; track via the new `pref_compression_sent` / `pref_video_codec_type_sent` snapshot fields. Add the fields to `DisplaySnapshot` and populate in `update_snapshot`. Extend `test_display_snapshot_serialises` to assert the new fields appear in JSON. |
| 7C | low | haiku | none | **Docs.** Update `ARCHITECTURE.md`'s display-channel capability table to include bits 6 and 12. Mention the preference messages briefly in `docs/troubleshooting.md` under a new "What ryll asks the server to do" subsection (compression + codec preferences, fired once at link-up; visible via the new snapshot fields). Cross-link from `docs/libvirt-spice-recommendations.md`'s `image-compression='auto_lz'` section — note that ryll now actively requests this preference so server-side `auto_lz` and the client preference will agree. Verify `pre-commit run --all-files`. |
| 7D | — | — | — | **Operator smoke test.** One ryll session against any existing test instance. Check the resulting `channel-state.json` shows `pref_compression_sent: true` and `pref_video_codec_type_sent: true`. If the server captures the link-up pcap (or if you run a brief 005-style debug session), confirm the two messages appear on the wire by their opcode (103, 104). |

## Open question

**Should `PREFERRED_VIDEO_CODEC_TYPE` include VP8/VP9 fallback?**
spice-gtk advertises all four when GStreamer plugins are present.
For v1 keep the list to H264 + MJPEG since those are the two
codecs we actually decode client-side. Revisit if VP8/VP9
decoders are wired in.

## Success criteria

- `make build && make test && make lint && pre-commit run --all-files` clean.
- Display-channel cap advertisement includes bits 6 and 12.
- `channel-state.json` from a successful session shows both
  `pref_*_sent: true`.
- Pcap captures `display:` opcodes 103 and 104 immediately after
  the channel reaches STATE_LINKED.
