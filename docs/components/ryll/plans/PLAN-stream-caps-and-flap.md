# Video stream capability expansion and flap diagnostics

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
H.264 / VP8 codecs, LZ4 compression, vdagent), research as
needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), `/srv/src-reference/spice/spice-html5/`
(reference JS client), and `/srv/src-reference/spice/spice/`
(server-side SPICE in `server/`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table in this master plan under the Execution
section.

I prefer one commit per logical change, and at minimum one
commit per phase.

## Situation

Test session 002 (and the 002a follow-up that exercised the new
per-stream instrumentation landed on this branch) revealed that
playing video on a guest VM via the SPICE display channel from
macOS produces audible audio but only sporadic display updates.
The earlier sub-investigation, supported by:

- `traffic.pcap` showing long zero-bandwidth gaps from the server,
- the new per-stream counters in `DisplaySnapshot` showing
  `streams_created_total == streams_destroyed_total == 2` over a
  32 s session with `streams_active == []` at snapshot time,
- the `ryll-console-output.txt` log showing six `STREAM_CREATE`
  → `STREAM_DESTROY` cycles (each stream alive ~1–2 s, with
  10–15 s silence between cycles),

pins the symptom on the server's `RED_STREAM_TIMEOUT = 1 s`
(`/srv/src-reference/spice/spice/server/video-stream.h:34`)
combined with the guest producing frames in bursts rather than
continuously. The server creates an MJPEG stream when the
streaming-video heuristic fires, sends a burst, sees no new
frames for 1 s, and destroys the stream
(`server/video-stream.cpp:1031-1044`).

No client behaviour triggers the destruction — the relevant
server code paths are bound only to the timeout, surface
changes, and explicit teardown. The lifecycle is independent
of `STREAM_REPORT` (which only feeds adaptive bitrate in
`server/dcc.cpp:867-880`).

That said, ryll's display-channel capability set in
`shakenfist-spice-protocol/src/constants.rs:116-127` is
notably minimal compared to spice-gtk
(`/srv/src-reference/spice/spice-gtk/src/channel-display.c:975-993`)
and spice-html5 (`spice-html5/src/spiceconn.js:163-169`). We
advertise `SIZED_STREAM | MONITORS_CONFIG | COMPOSITE | A8_SURFACE`
only. We do not advertise:

| Cap | spice-gtk | spice-html5 |
|---|---|---|
| `STREAM_REPORT` (4) | ✓ when adaptive | always |
| `LZ4_COMPRESSION` (5) | ✓ | — |
| `PREF_COMPRESSION` (6) | ✓ | — |
| `MULTI_CODEC` (8) | ✓ | ✓ |
| `CODEC_MJPEG` (9) | ✓ (built-in) | always |
| `CODEC_VP8` (10) | ✓ (gstreamer) | ✓ (WebM) |
| `CODEC_H264` (11) | ✓ (gstreamer) | — |
| `PREF_VIDEO_CODEC_TYPE` (12) | ✓ | — |
| `CODEC_VP9` (13) | ✓ (gstreamer) | — |
| `CODEC_H265` (14) | ✓ (gstreamer) | — |

The most consequential gap is the codec set: without `MULTI_CODEC`
and `CODEC_H264` / `CODEC_VP8`, the server falls through to the
legacy MJPEG-only path
(`server/video-stream.cpp:813-816`). At 1600×1200 each MJPEG
frame is 150–300 KB; H.264 IDR ≈ 30 KB, P-frames a few KB. A
modern codec would also be more efficient on the server side
and may interact better with its stream-detection heuristic.

`openh264` is already a workspace dependency (Cargo.lock — pulled
in by `shakenfist-spice-webrtc`), so the H.264 path is not from
zero.

### On vdagent diagnostics

The spice in-guest agent protocol
(`/srv/src-reference/spice/spice-protocol/spice/vd_agent.h`) is
purely functional. Defined message types cover clipboard
(`VD_AGENT_CLIPBOARD*`), mouse state (`VD_AGENT_MOUSE_STATE`),
monitor configuration (`VD_AGENT_MONITORS_CONFIG`,
`VD_AGENT_DISPLAY_CONFIG`), file transfer
(`VD_AGENT_FILE_XFER_*`), audio volume sync
(`VD_AGENT_AUDIO_VOLUME_SYNC`), and graphics device
identification (`VD_AGENT_GRAPHICS_DEVICE_INFO`). **None
expose guest-side diagnostic information** — there is no opcode
for guest CPU/memory state, render-pipeline health, QXL driver
status, or per-process information. Guest-side troubleshooting
remains an SSH / virsh / host-metrics problem outside ryll's
scope.

What we *can* infer from the agent without protocol extensions:

1. **Connection state** — already surfaced via the `Guest agent
   connected` / `Guest agent disconnected` notifications.
2. **Agent responsiveness** — we could measure the latency of
   `VD_AGENT_REPLY` to `VD_AGENT_MONITORS_CONFIG` as a proxy for
   guest-side stall (a stuck guest typically delays REPLY).
3. **GraphicsDeviceInfo presence** — if the guest is sending
   `VD_AGENT_GRAPHICS_DEVICE_INFO` (cap `GRAPHICS_DEVICE_INFO`,
   bit 17), it has a working enumeration path to the device.

Of these, only the agent-responsiveness probe would add new
diagnostic signal. Worth a small follow-up after this plan;
not in scope here.

### Update from test session 002b (post-phase-1)

The first dogfood session under the new STREAM_REPORT
instrumentation (`test-session-002b`, ryll commit `241ba13f`)
ran against a larger 2048×1152 instance and surfaced a
distinct symptom: drag/resize gestures are unresponsive even
without video playback. The per-stream snapshot data this
phase-1 work added pins the cause: MJPEG decode in the
pure-Rust `jpeg-decoder` crate takes 76–175 ms per frame at
2048×1152, so frames arrive late
(`last_report_last_frame_delay: -142 to -504 ms` across eight
of ten destroyed streams), the spice-server's streaming
heuristic loses confidence, and the stream gets destroyed
within 2 seconds. During the long gaps between streams, the
screen visibly freezes. Network is clean (0 retransmits,
27 KB average packet size), client CPU is idle (process 0%),
and the user's host-side `top` reading on sf-4 agreed —
nothing is CPU-bound at the wire level.

This pushed a new piece of work into the plan: a per-platform
JPEG decoder selector (phase 3, ahead of the H.264 work in
phase 4) so we close the decode-latency gap on every platform
before adding more codecs on top.

## Mission and problem statement

Implement four SPICE display capabilities that ryll should
advertise and use, replace the pure-Rust MJPEG decoder with a
per-platform optimal selector (driven by session 002b's
finding above), and add a UI signal that surfaces the
spice-server's stream-flapping behaviour to operators triaging
"video doesn't play" reports.

In priority order:

1. **`STREAM_REPORT` (cap 4)** — receive `STREAM_ACTIVATE_REPORT`,
   send periodic `SPICE_MSGC_DISPLAY_STREAM_REPORT` (display-client
   opcode 102) so the server's encoder gets adaptive-bitrate
   feedback. Mostly counters we already have in `StreamState`.
2. **`LZ4_COMPRESSION` (cap 5)** — advertise, accept LZ4-encoded
   images from the server. Modest bandwidth/CPU win on the
   static-UI Zlib/GLZ path. `lz4_flex` or similar is the
   obvious decoder choice.
3. **Fast per-platform MJPEG decode** — replace the pure-Rust
   `jpeg-decoder` crate with a runtime-selected best-of-breed
   decoder per platform (ImageIO on macOS, WIC on Windows,
   VA-API on Linux with vendored libjpeg-turbo as the always-
   available baseline, pure-Rust as the universal fallback).
   Inserted ahead of the codec work after session-002b
   evidence that pure-Rust JPEG decode at 2048×1152 is the
   dominant client-side bottleneck.
4. **`MULTI_CODEC` + `CODEC_MJPEG` + `CODEC_H264` (caps 8/9/11)**
   — advertise multi-codec, keep MJPEG as fallback, decode H.264
   stream data via `openh264` (already in tree). Significantly
   reduces bandwidth and may stabilise the server's stream
   lifecycle for video workloads.
5. **`PREF_COMPRESSION` + `PREF_VIDEO_CODEC_TYPE` (caps 6/12)** —
   send the corresponding preference messages once on link-up so
   the server picks the codec/compression we prefer. Cheap once
   the multi-codec path exists.

And one UI feature:

6. **Stream-flap notification** — detect rapid create/destroy
   cycles in the per-stream snapshot data we just landed, and
   raise a one-shot notification (in the existing
   `NotificationStore`) so the operator knows the SPICE server
   is bouncing the video stream. Include the relevant counts and
   a hint that this typically indicates guest/server rather than
   client trouble.

## Open questions

These should be resolved before, or during, the relevant phase.
Items marked **decide now** belong in this master plan; items
marked **decide in phase** belong in the phase plan.

1. **(decide now) Codec order: H.264 first, both H.264 and VP8,
   or VP8 first?** `openh264` is already pulled in; adding it
   adds no new dependencies. VP8 would require `libvpx` or a
   pure-Rust decoder (none mature). **Recommendation: H.264
   only in phase 4; defer VP8/VP9/H265 to follow-up.** Capture
   that explicitly in the plan.

2. **(decide now) Are we OK adding `openh264` as a runtime
   dependency for the client binary?** It already ships through
   the `--web` path but the GUI client doesn't link it today.
   **Recommendation: yes** — it's already an audit-clean
   dependency in the workspace.

3. **(decide in phase 1) STREAM_REPORT timing.** spice-gtk sends
   when `num_frames >= max_window_size` (5), OR
   `timeout_ms` (1000) elapsed since last report, OR three
   consecutive drops. The fields and cadence are well-defined
   in `/srv/src-reference/spice/spice-gtk/src/channel-display.c:1534-1589`
   — we should match. **Decision: mirror spice-gtk semantics
   exactly.**

4. **(decide in phase 2) Where in the image-decode dispatch does
   `LZ4` insert?** Our image-type dispatch lives in
   `shakenfist-spice-renderer/src/channels/display.rs` and the
   `shakenfist-spice-compression` crate. The phase plan needs to
   identify the right hook.

5. **(decide in phase 4) Where does H.264 decode run — inline in
   the display-channel task, or on a dedicated `spawn_blocking`
   task like the encoder?** H.264 decode is meaningfully heavier
   than MJPEG. Inline would simplify the data flow; offloaded
   would protect the channel task from stutter. Investigate in
   the phase plan.

6. **(decide in phase 6) Flap-detection heuristic.** Candidates:
   "N streams destroyed in M seconds with mean lifetime < T"
   (e.g. ≥3 destroys in 30 s, mean lifetime < 3 s). The phase
   plan picks the constants and the cool-down period for the
   notification. **Starting point: ≥3 destroys in 30 s window
   with mean lifetime < 3 s, one-shot per 60 s cool-down.**

7. **(open) Should we cross-validate against virt-viewer before
   shipping H.264?** Yes — see `Future work`. The phase 4 plan
   should call for a manual test against the same VM under both
   ryll and virt-viewer to confirm the flap pattern is or isn't
   shared. Cheap and informative.

## Execution

Eight phases, sequenced so cheap-and-independent work lands
first; per-platform decoder work lands before multi-codec so
the JPEG decode floor is healthy before we add H.264; vdagent
probe is independent and sits late so the documentation phase
covers it. Phase 3 (fast JPEG decode) was inserted after
session 002b showed that MJPEG decode in the pure-Rust
`jpeg-decoder` crate is the dominant bottleneck on macOS at
2048×1152 (76–175 ms per frame).

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. STREAM_REPORT | [completed/PLAN-stream-caps-and-flap-phase-01-stream-report.md](/components/ryll/plans/completed/PLAN-stream-caps-and-flap-phase-01-stream-report/) | Complete | — |
| 2. LZ4 compression | [PLAN-stream-caps-and-flap-phase-02-lz4.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-02-lz4/) | Code landed; smoke test (2C) folded into phase 3 step 3H | — |
| 3. Fast JPEG decode | [PLAN-stream-caps-and-flap-phase-03-jpeg-decoders.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-03-jpeg-decoders/) | Code landed (3A-3G). Session 006 follow-up revealed still-image JPEG path missed the wiring (was on pure-Rust at ~263 ms/frame); fix landed. 3H replaced with the 007 still-image JPEG smoke matrix (one shared guest, three client OSes) — pending operator runs. | — |
| 4. Channel diagnostics audit + playback observability | [completed/PLAN-stream-caps-and-flap-phase-04-channel-diagnostics.md](/components/ryll/plans/completed/PLAN-stream-caps-and-flap-phase-04-channel-diagnostics/) | Complete (4G verified in session 002g: `data_packets_received == data_packets_decoded`, no decode failures, underruns visible — instrumentation distinguishes the four failure modes as designed) | — |
| 5. Auto-snapshot bug-report mode | [PLAN-stream-caps-and-flap-phase-05-auto-snapshot.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-05-auto-snapshot/) | Code landed (5A-5B); 5C operator smoke test pending | — |
| 6. Multi-codec + H.264 | [PLAN-stream-caps-and-flap-phase-06-h264.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-06-h264/) | Code landed (6A-6E); 6F operator smoke test pending (needs an H.264-capable spice-server build to actually exercise the new path) | — |
| 7. Preference messages | [PLAN-stream-caps-and-flap-phase-07-pref-messages.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-07-pref-messages/) | Code landed (7A-7C); session 006 measurement drove AUTO_LZ → AUTO_GLZ revert (server stopped using GLZ entirely under AUTO_LZ; +25% bytes_in). 7D smoke folded into the next 005-style run. | — |
| 8. Live streaming indicator + flap notification | [PLAN-stream-caps-and-flap-phase-08-streaming-indicator.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-08-streaming-indicator/) | Code landed. | — |
| 9. Vdagent responsiveness probe | [PLAN-stream-caps-and-flap-phase-09-vdagent-probe.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-09-vdagent-probe/) | Code landed (9A-9D). 9E operator smoke (deliberately freeze the guest agent to verify the Warn notification fires) is pending. | — |
| 10. Documentation | (no separate plan — docs catch-all dispatched directly) | Complete. ARCHITECTURE.md capability table + README CLI flag docs + troubleshooting JPEG-decoder subsection landed across three commits (faa8ebd9 / c4849822 / dfc59950). Phase-9D's vdagent docs already covered the agent-probe surface. | — |
| 11. Remove spurious-PONG keepalive | [PLAN-stream-caps-and-flap-phase-11-remove-pong-keepalive.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-11-remove-pong-keepalive/) | 11A landed (main channel); 11B doc cleanup landed (channel-diagnostics-audit + plan addendum); inputs-channel `send_idle_keepalive` kept (decision recorded in phase 11 plan — different mechanism + no visible side effects + cross-channel-idleness hypothesis); 11C long-idle soak pending. | — |
| 12. Bounded image cache | [completed/PLAN-stream-caps-and-flap-phase-12-bounded-image-cache.md](/components/ryll/plans/completed/PLAN-stream-caps-and-flap-phase-12-bounded-image-cache/) | **Complete.** 12A-12C landed; 12D smoke FAILED (revealed GlzDictionary was a second unbounded cache the snapshot was summing in); 12E (bound GLZ), 12F (split snapshot fields), 12G (docs) all landed; 12H verified in session 005b — `glz_dictionary_bytes` held at 247 MiB / 256 cap across 67 snapshots over 670 s with eviction firing, `image_cache_bytes` stayed at 0, no RSS drift. | — |
| 13. Investigate intermittent server-side streaming | [PLAN-stream-caps-and-flap-phase-13-streaming-intermittency.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-13-streaming-intermittency/) | **Parked.** Session 006 confirmed the trace-ring/VRAM diminishing-returns curve (165→85→77 OOMs/min at 64/128/256 MiB) but uncovered a bigger blocker: the 1024×768 YouTube video almost never crosses `is_stream_start` (1–2 video stream creates per 10 min vs ~100 cursor / scrollbar creates). All four 006 bundles show `streams_created_total = 0` client-side. Findings folded into the phase 13 plan's "Session 006 findings" section. Resume after non-video phases close. | — |
| 14. Stop status-bar pointer events leaking into the guest | [PLAN-stream-caps-and-flap-phase-14-statusbar-pointer-leak.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-14-statusbar-pointer-leak/) | Code landed. | — |
| 15. Track down `build_tcp_frame: payload too large` warns | [PLAN-stream-caps-and-flap-phase-15-build-tcp-frame-warn.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-15-build-tcp-frame-warn/) | 15B (one-shot backtrace) landed; no fires across 006 bundles. Awaiting fresh reproduction. | — |
| 16. Evaluate guest driver options for video streaming | [PLAN-stream-caps-and-flap-phase-16-qxl-viability.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-16-qxl-viability/) | **Parked.** Concept stub. Resume alongside phase 13 after non-video phases close. | — |
| 17. Patched libspice-server for hypothesis validation | [PLAN-stream-caps-and-flap-phase-17-patched-libspice-validation.md](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-17-patched-libspice-validation/) | **Parked.** Value uncertain after 006: bumping `NUM_TRACE_ITEMS` 8→128 helps cursor / scrollbar flap, but does not address why the YouTube video itself isn't a stream. Hold off on the .deb build until the predicate question is answered. | — |
| 18. Push audit | PLAN-stream-caps-and-flap-phase-18-push-audit.md | Not started | — |

### Closeout — non-video work remaining

Per session 006 we are parking the video-bottleneck phases
(13 / 16 / 17) and closing out the rest of the master plan
before re-assessing. The remaining open items are:

- **Phase 2 / 3** — code landed; 2C smoke folded into 3H,
  the per-platform JPEG-decode smoke test. Operator-side
  work.
- **Phase 5C** — auto-snapshot operator smoke test.
  Functionally validated by every 005-onwards session
  (auto-snapshots ARE the bundle source). Likely just
  marking complete; verify and close.
- **Phase 6F** — H.264 wire-smoke. Needs an H.264-capable
  spice-server build. Operator-side; gated on the spice-
  server side rather than ryll. Mark blocked-on-external
  rather than open.
- **Phase 9** — vdagent responsiveness probe. Independent
  of video work. Worth picking up.
- **Phase 10** — documentation catch-all. Last of the
  work phases; phase 18 audits what they add up to.
- **Phase 11B / 11C** — inputs-channel keepalive decision
  + long-idle soak.
- **Phase 14 / 15** — landed / instrumented; nothing for
  the management session to chase until / unless
  reproductions come back.

Suggested ordering for closeout: 5C/2C/3H closures first
(mostly bookkeeping against existing sessions), then 11B
+ 11C, then phase 9, then phase 10 to roll up the new
caps + the phase-13 / 16 / 17 parking into the docs. After
that, re-open phase 13 with the "why isn't the video a
stream" question and decide whether to chase the
predicate read or the client-side instrumentation first.
Phase 18, the push audit, closes the plan out once the
phases that are going to land have landed.

Per-phase intent:

- **Phase 1 — STREAM_REPORT.** Add `display_client::STREAM_REPORT
  = 102`, capability advertisement bit 4, handler for
  `STREAM_ACTIVATE_REPORT` that captures
  `(stream_id, unique_id, max_window_size, timeout_ms)` into the
  matching `StreamState`, and a small ticker that emits
  `SpiceMsgcDisplayStreamReport` on the cadence rules. Reuse the
  per-stream counters already in `StreamState`. Add fields to
  `StreamSnapshot` for `last_report_*` so a bug report shows
  whether we ever sent one. Verify against
  spice-gtk's `display_update_stream_report` for field semantics.
  Recommended planning effort: **high** (the spec needs to be
  read carefully; field semantics matter).

- **Phase 2 — LZ4_COMPRESSION.** Advertise cap 5. Wire LZ4
  decoding into the image-decode dispatch (server may now send
  images with the LZ4 type). Decompressor crate selection:
  `lz4_flex` is pure-Rust and audit-clean. Unit tests with
  vectors from `spice-common` if available, or round-trip
  encoded by the same library. Recommended planning effort:
  **medium** (well-defined; the only judgment call is hook
  placement).

- **Phase 3 — Fast JPEG decode.** Replace the pure-Rust
  `jpeg-decoder` crate (currently called from
  `shakenfist-spice-renderer/src/channels/display.rs::decode_mjpeg_frame`)
  with a platform-optimal selector chain: ImageIO on macOS,
  WIC on Windows, VA-API (dlopen-probed) on Linux with
  vendored libjpeg-turbo (`mozjpeg` crate) as the always-
  available baseline, and pure-Rust as the universal
  fallback. Driven by session 002b's finding that MJPEG
  decode at 2048×1152 takes 76–175 ms in the pure-Rust path,
  causing frames to arrive late, the spice-server's
  streaming heuristic to lose confidence, and the user to
  see frozen displays between streams. New `JpegDecoder`
  trait + `best_for_platform()` selector in
  `shakenfist-spice-compression`; per-stream
  `mjpeg_decoder_backend` and aggregate
  `mjpeg_decode_recent_*` fields in bug reports. Recommended
  planning effort: **high** (cross-platform, four backend
  implementations, COM threading on Windows, dlopen + JPEG
  header parsing for VA-API).

- **Phase 4 — Channel diagnostics audit + playback
  observability.** Driven by sessions 002d/002e, where the
  user reported audio worked for Gnome event sounds but went
  silent during long video playback. The console logs showed
  `playback: START → Opus decoder initialized → audio output
  started`, then nothing — no errors, no packet counts, no
  visibility into whether samples actually reached the
  device. We hit the same observability gap in session 002
  (display channel) and patched it under duress in phases 1
  and 3; this phase is the systemic fix. Audit every channel
  for "what diagnostic surface does the bug report
  currently expose, and what is missing?", define a
  consistent baseline, and close the gaps — with playback
  detailed enough to characterise the audio-silence symptom
  in a single bug report. Today's gap snapshot:

  | Channel | Snapshot fields | Channel-specific signals |
  |---|---|---|
  | display | 40 | rich (post-phase-3) |
  | main | 15 | yes (mm_time, session_id, keepalive) |
  | inputs | 14 | yes (motion_count, recent_events) |
  | cursor | 14 | yes (cache_entries) |
  | playback | 8 | **none** — only generic transport |
  | usbredir | 8 | **none** |
  | webdav | 8 | **none** |
  | record | 0 | channel skipped at link time |

  Scope:
  - Audit doc capturing the matrix above plus an explicit
    "what would I want to see in a bug report for this
    channel?" list per channel.
  - Define and document the *minimum diagnostic baseline*
    every channel should publish on top of the transport
    common (recent-action ring, last-action timestamp,
    per-message-type counters, error counts).
  - `PlaybackSnapshot` gains audio-specific fields: DATA
    packets received, packets decoded, decode failures,
    decoder errors, samples enqueued, samples consumed,
    ring-buffer underruns/overruns, last START/STOP info
    (rate, channels, codec, mm_time), volume/mute state,
    last_data_recv_ts, recent decode-duration ring.
  - `UsbredirSnapshot` gains device-specific fields: USB
    redirect packet counts by direction, active redirected
    devices, last device add/remove.
  - `WebdavSnapshot` gains HTTP-specific fields: request
    count, response-body bytes, active session count.
  - Decide whether `record` channel gets a snapshot or stays
    explicitly skipped with a documented justification.
  - Bug-report writer extended to include the new fields;
    `test_display_snapshot_serialises`-style tests extended
    for each new channel snapshot.

  Out of scope:
  - Implementing the `record` channel itself (we don't
    expose mic capture in any UI today).
  - Pretty-printing the new fields in the in-app stats
    panel — this is bug-report observability, not live UI.

  Recommended planning effort: **medium**. The audit is
  straightforward; the playback work needs care (the
  audio thread is a separate native thread, snapshot
  writes from a tokio task) and is the highest-leverage
  fix.

- **Phase 5 — Auto-snapshot bug-report mode.** Driven by
  session 002f, where audio worked for that run and so no
  bug report was filed — leaving us with no diagnostics to
  compare against the 002d/002e silence symptom. Manual
  bug-report-while-symptom-is-active is fundamentally
  fragile for intermittent issues: you only know to hit
  the button after you notice, and by then the relevant
  window has often passed. Add a flight-data-recorder mode
  that fires a full bug report (channel-state.json, pcap,
  metadata, runtime-metrics — same artefact shape as a
  manual report) every N seconds into a rolling subdirectory
  with a fixed cap. Operator sets it once at session start
  via a CLI flag (`--auto-snapshot-interval`) and walks
  away; whatever happens during the run is captured by
  construction. Per operator direction, pcap stays in
  auto-snapshots — the disk cost (~700 KiB per snapshot,
  ~14 MiB at 20-snapshot cap) is acceptable for the
  diagnostic value. Recommended planning effort: **low**
  (small, well-scoped; the existing `BugReport::new` +
  `write_zip` already do all the hard work — this phase
  is mostly about plumbing them to a tokio interval task
  with file rotation).

- **Phase 6 — Multi-codec + H.264.** Advertise caps 8 (MULTI_CODEC),
  9 (CODEC_MJPEG), and 11 (CODEC_H264). Hook H.264 decoding into
  the existing `STREAM_DATA` / `STREAM_DATA_SIZED` path keyed on
  `StreamState::codec_type`. Use `openh264` (already in
  Cargo.lock) for the decoder. Reuse all of the per-stream
  instrumentation we already have. Decide on inline vs offload
  during the phase plan (see open question 5). Important: keep
  MJPEG as the fallback so a server that rejects multi-codec
  still works. Recommended planning effort: **high** (decoder
  threading, codec-specific framing, and the first time we add
  a video codec to the GUI binary).

- **Phase 7 — Preference messages.** Add `display_client::PREFERRED_COMPRESSION`
  (opcode 103) and `display_client::PREFERRED_VIDEO_CODEC_TYPE`
  (opcode 105 — 104 is GL_DRAW_DONE). Advertise caps 6 and 12. Send the preference
  messages once on link establishment. spice-gtk does this in
  `channel-display.c` near the init handler. Recommended
  planning effort: **medium** (mechanical once the cap plumbing
  is in place from earlier phases).

- **Phase 8 — Live streaming indicator + flap
  notification.** Two complementary UI affordances that
  share the same data source (`streams_active` and
  `streams_recently_destroyed` on the display channel):

  - **Live status-bar icon.** A small video-camera-style
    icon in the status bar whose colour reflects current
    streaming state: grey/dim when no streams are
    active, green when at least one stream is active and
    healthy, amber when a stream was recently destroyed
    (within the last few seconds), red when the flap
    heuristic below fires. Hovering shows a per-stream
    tooltip: codec, surface size, frames decoded, current
    lifetime. Updates live (driven by `update_snapshot`
    cadence, no extra polling). Operator can watch a
    session and see streams come and go in real time —
    directly useful for the phase 13 streaming-
    intermittency investigation, where being able to say
    "the stream was alive when the video started and died
    after exactly N seconds" without ad-hoc bug-report
    capture would speed every test cycle.
  - **Flap notification.** A small per-channel watcher
    (likely a tokio task or a tick inside
    `update_snapshot`) that examines the
    `streams_recently_destroyed` ring. If ≥3 streams
    destroyed in the last 30 s with mean lifetime < 3 s,
    push a `NotifySeverity::Warn` notification via
    `push_notification` with
    `NotificationSource::Internal` (one-shot, 60 s
    cool-down) saying something like "Server is rapidly
    creating and tearing down video streams ({N} cycles
    in {Ms}, mean lifetime {Ts}); this usually means
    the guest is producing frames in bursts." The
    notification is the alert for batch-job operators
    who aren't watching the status bar; the icon is the
    primary signal for interactive use.

  Recommended planning effort: **medium**. Icon + flap
  notification share a derived-state computation; both
  read from the same display-channel snapshot fields
  that already exist post-phase 1. UI integration follows
  existing stats-panel + notification patterns.

- **Phase 9 — Vdagent responsiveness probe.** The spice in-guest
  agent has no diagnostic message types of its own (see the
  *On vdagent diagnostics* note in `Situation`), but two
  client → agent messages are acknowledged by `VD_AGENT_REPLY`:
  `VD_AGENT_MONITORS_CONFIG` (Linux + Windows agents) and
  `VD_AGENT_DISPLAY_CONFIG` (Windows only). `VDAgentReply` is
  `{ uint32 type, uint32 error }` where `type` echoes the
  request opcode, so we can correlate replies to requests.

  Mechanism: instrument the existing send/receive path for
  `VD_AGENT_MONITORS_CONFIG` (we already send this on window
  resize and at session start) to record send-timestamp and
  reply-lag. Add an idle probe that re-sends the current
  monitors config every N seconds when no other monitors
  config has been sent for a while — the guest should treat
  an identical config as a no-op, and if it doesn't, that's
  itself diagnostic. Surface on `MainSnapshot`:

  - `agent_request_count: u32` — outbound MONITORS_CONFIG sends
  - `agent_reply_count: u32` — `VD_AGENT_REPLY` messages received
  - `agent_reply_error_count: u32` — replies with `error != VD_AGENT_SUCCESS`
  - `last_agent_reply_ts_secs: Option<f64>`
  - `last_agent_reply_lag_us: u32`
  - `recent_agent_reply_lag_us: VecDeque<u32>` — bounded ring
    (cap ≈ 16) for min/max/mean
  - `outstanding_agent_request_count: u32` — sends without
    matching reply yet (informational; high values suggest
    a stuck agent)

  Optional UI: raise a `NotifySeverity::Warn` notification if
  `outstanding_agent_request_count > 0` for more than 5 s
  after a probe send. Mirror the cool-down pattern from
  phase 6 to avoid noise. Recommended planning effort:
  **medium** (small surface area; the only judgment call is
  probe cadence and the no-op assumption).

- **Phase 10 — Documentation.** Update `ARCHITECTURE.md`
  capability tables, `AGENTS.md` reference list if a new
  external ref was added, `README.md` if user-visible behaviour
  changed, and add a "video troubleshooting" section to
  `docs/troubleshooting.md` that explains the flap notification,
  the vdagent probe fields, and links to the bug-report fields
  a user should attach. Recommended planning effort: **low**.

- **Phase 11 — Remove spurious-PONG keepalive.** Driven by
  session 002c, where the operator's qemu log showed a
  cadence of `Spice: main:0 (...): invalid net test stage,
  ping id 0 test id 0 stage 0` warnings every ~15 s. Traced
  to `send_idle_keepalive()` in `main_channel.rs:1458`,
  added in commit `cfd4a20c` (2026-05-09) as a band-aid for
  the K1 main-channel wedge. The K1 root cause was fully
  fixed in commit `370d8ce5` (2026-05-11) by dropping the
  abandoned temp event channel — the keepalive band-aid is
  now redundant and leaks visible warnings into the server
  log on every session. Remove `send_idle_keepalive`, the
  `KEEPALIVE_IDLE` constant, the select-arm that calls it,
  the `client_keepalive_send_count` /
  `last_client_keepalive_send_ts_secs` fields, and the
  matching `MainSnapshot` fields. Verify with a long-idle
  (≥10 minutes) session against `sf-4` (the test target)
  that main does not disconnect — the heartbeat log
  (`main: heartbeat T+...`) should keep firing without any
  keepalive. Recommended planning effort: **low** (single
  commit; the risk is that K1 reproduces, which is what
  the long-idle smoke test rules out).

- **Phase 12 — Bounded image cache.** Driven by session
  002g, where auto-snapshot revealed `image_cache_bytes`
  growing from 884 MiB → 1843 MiB → 2803 MiB across the
  three 30 s snapshots — a linear 30 MiB/s leak driven by
  full-frame `ZlibGlzRgb` payloads (1920×1472 RGBA ≈
  10.7 MiB each) that the server kept marking with
  `IMAGE_FLAGS_CACHE_ME`. We honour every cache-me request
  via `display.rs:2204
  (self.image_cache.insert(img.image_id, img.pixels.clone()))`
  with no upper bound and no LRU eviction — the cache only
  shrinks when the server sends an explicit `inval_*`
  message, which for video workloads it rarely does. At
  the observed rate, a 10-minute video would consume
  ~18 GiB and OOM the client on any reasonable Mac. The
  server's over-eager `CACHE_ME` flagging on transient
  frames is a server-side decision; we have to defend
  client-side. Scope: replace the unbounded `HashMap<u64,
  Vec<u8>>` with a size-bounded LRU (cap by total bytes,
  default ~256 MiB, operator-overridable via CLI flag);
  on insert evict oldest entries until under cap; surface
  `image_cache_evictions_total: u64` and
  `image_cache_evicted_bytes_total: u64` on
  `DisplaySnapshot` so an operator can see the eviction
  pressure in a bug report. Honour every existing
  `inval_*` path unchanged — this only adds eviction on
  insert, not new invalidation logic. Recommended planning
  effort: **low** (well-scoped; the `lru` crate is a
  drop-in replacement, or a hand-rolled `VecDeque` +
  `HashMap` works fine).

  Open observation worth keeping with this phase: session
  002g also showed zero MJPEG streams (`streams_created_total:
  0`) despite the operator NOT changing the server config
  between 002e (which streamed) and 002g (which didn't).
  Same Debian 11 QXL guest, same `streaming-video=all`
  setting. This suggests the server's streaming heuristic
  is non-deterministic with respect to workload / timing
  / content, not just config. Worth noting for the
  flap-notification phase (phase 8) and for any
  future investigation into why streaming is intermittent
  on QXL guests.

- **Phase 13 — Investigate intermittent server-side
  streaming.** Driven by sessions 002e / 002g / 002h,
  which are now a 2-out-of-3 reproduction of "server
  stops streaming MJPEG/H.264 and falls back to
  full-frame ZlibGlzRgb blasts" on the **same** Debian
  11 QXL guest with the **same** server config
  (`streaming-video=all`). 002e streamed for 17 s with
  135 MJPEG frames at 7.9 fps; 002g and 002h streamed
  zero frames over their entire runs. Once H.264 was
  wired (phase 6) the natural assumption was that 6F
  would land via the next dogfood — but the server
  never elected to stream the video at all, so the
  H.264 path remains untested on the wire and the user
  perceives no improvement in video performance. This
  blocks both (a) the 6F smoke test and (b) any
  meaningful video-performance comparison across guest
  configurations.

  This phase is an **investigation, not a code
  delivery**: the bug (if it is a bug) is on the
  server side, and the goal is to identify whether
  ryll is doing something that confuses the server's
  stream-create heuristic, whether the spice-server's
  heuristic itself is misfiring, or whether the
  workload-side conditions for streaming are subtle
  enough that "same guest, same config" reproductions
  vary by accident. Scope:

  - Read `spice/server/video-stream.cpp` (especially
    `red_stream_input_fps_timeout_callback` and
    `mjpeg_encoder_can_drop_stream` / the streaming-mode
    decision points around `display-channel.cpp`'s
    `display_channel_create_stream` site) to understand
    the heuristics ground-truth.
  - Compare the pcap traces from 002e (which streamed)
    against 002g/002h (which didn't) at the protocol
    level. Look for differences in: monitor config
    sequence, ack cadence, what we acknowledge first,
    how many surface_create messages we send before
    interacting, anything client-side that could
    influence the server's "is this a streamable
    region" detector.
  - Enable spice-server-side debug logging
    (`SPICE_DEBUG_LEVEL=2` or
    `G_MESSAGES_DEBUG=all` against a libspice-server
    debug build) on the test host and capture a side-
    by-side log of a streaming and a non-streaming
    session of the same workload.
  - Build a minimal-reproduction recipe: a fixed
    workload (specific video file, specific player) +
    fixed VM start sequence that reliably triggers
    one outcome or the other. Even non-determinism
    is useful information once it's pinned to a
    workload.
  - Document findings: either (a) a server-side bug
    that should be filed upstream against spice-server
    with the minimal reproducer, (b) a workload-side
    condition we can document for operators, or (c) a
    client-side behaviour we can adjust to be more
    streaming-friendly.

  Out of scope: actually patching spice-server. If we
  find a bug, file upstream and apply a local workaround
  via libvirt config (mentioned in
  `docs/libvirt-spice-recommendations.md`). If the issue
  is client-side, it lands as its own follow-up phase
  rather than being shoehorned into this investigation.

  Recommended planning effort: **high** (open-ended
  investigation; success criterion is "we know which of
  the three categories the bug falls into," not "we
  shipped code"). Output: a writeup at
  `docs/spice-server-streaming-investigation.md` plus
  whatever upstream-issue links or libvirt
  recommendations updates follow.

- **Phase 14 — Stop status-bar pointer events leaking into
  the guest.** Driven by session 004f, where the operator
  observed that clicks on the egui status-bar volume widgets
  also registered inside the guest. Root cause is visible in
  `ryll/src/app.rs:3814-3885`: the `MouseMotion` / `MouseMove`
  path correctly gates on `response.hover_pos()` (constrained
  to the SPICE surface's `egui::Image` rect), but the button
  and scroll paths use `ctx.input(...)` which sees every
  pointer event in the egui window regardless of which widget
  is under the cursor, and forwards `MouseDown` / `MouseUp` /
  scroll-wheel events to the guest at `last_mouse_pos`
  (the *last* image-relative coordinate). Result: clicking
  the volume slider, mute button, bug-report widgets, or any
  other status-bar control fires a phantom click into the
  guest at wherever the cursor was last over the image.

  Fix shape: replace the `ctx.input(...)` button-and-scroll
  block with `response.*` interrogation
  (`response.is_pointer_button_down_on()` for press state,
  `response.clicked_by()` / `response.dragged_by()` for
  edges, `response.hovered() && ctx.input(|i| i.smooth_scroll_delta)`
  for scroll), so press/release/scroll events only forward
  when the pointer is actually over the SPICE surface. Verify
  by clicking each status-bar control (volume slider, mute,
  reconnect, USB-device label, FPS label) while the guest has
  a focusable target in the click-through region (e.g. an open
  terminal) and confirming no click reaches the guest. Add a
  small input-channel test that the bug-dialog / region-select
  `input_suppressed` path still works (it shares the same code
  block — easy to regress).

  Scope is intentionally narrow: no new input semantics, no
  refactor of the input forwarding architecture. One file
  (`ryll/src/app.rs`), one logical change, one commit.

  Recommended planning effort: **low** (well-scoped UI bug
  fix; the only judgment call is whether to use
  `response.interact_pointer_pos()` or
  `ctx.input(|i| i.pointer.button_pressed(b))` gated on
  `response.contains_pointer()` — the phase plan picks).

- **Phase 15 — Track down `build_tcp_frame: payload too large`
  warns.** Driven by a live observation during session 004
  H.264 follow-up testing, where the Mac client logged:

  ```
  WARN build_tcp_frame: payload too large for IPv4 (2246044 bytes), dropping
  WARN build_tcp_frame: payload too large for IPv4 (2245427 bytes), dropping
  ```

  The K2 fix (`d95d4b3c`, 2026-05-12) introduced
  `capture::segment_payload` which chunks at
  `MAX_PAYLOAD = 65495` and is the only caller of
  `build_tcp_frame` in the current tree. Given segmentation,
  every `build_tcp_frame` invocation should arrive with
  `payload.len() ≤ 65495`, making the `ip_payload_len > 65515`
  check at `capture.rs:183` defensively unreachable.

  Two possibilities:

  1. **The running binary predates K2.** If `cargo build`
     on the Mac was done before pulling the K2 commit,
     the old `build_frame` path called `build_tcp_frame`
     with the whole SPICE message. A 2.2 MiB payload
     matches a single un-segmented display-channel message
     at ~1920×1440 RGBA. Confirm by reading
     `ryll --version` (embedded git sha) on the Mac and
     comparing against `93474db2` (which has K2). If
     pre-K2, the fix is a rebuild; phase closes.

  2. **There is a `build_tcp_frame` caller `grep` didn't
     find.** Possibilities: a sub-binary, a `cfg`-gated
     path, a hand-rolled frame builder using `etherparse`
     directly. If so, find it and route it through
     `segment_payload` like the other callers.

  Scope:
  - Confirm the running binary's sha (one shell command,
    no code changes).
  - If post-K2: instrument the warn with a one-shot
    backtrace (`tracing::warn!` + `std::backtrace::Backtrace::capture()`
    formatted with `{:?}` debug-level only on first hit,
    so subsequent firings don't spam) and reproduce.
    Find the caller; fix it.
  - Either way, once the actual call site is known and
    routes through `segment_payload`, demote the
    `if ip_payload_len > 65515` warn at `capture.rs:183`
    to a `debug_assert!` plus a `debug!` log. The check
    is defensive; once segmentation is the only path,
    a fired warn is a code bug to crash on in tests
    rather than a runtime condition to log around.

  Recommended planning effort: **low** (one of two
  outcomes is "rebuild and done"; the other is a
  one-file fix once the caller is located).

- **Phase 16 — Evaluate guest driver options for video
  streaming.** Driven by accumulated 002-005 evidence
  that the QXL guest driver is the substrate stream-flap
  arises on, and that the current
  `docs/libvirt-spice-recommendations.md` advice
  ("virtio-vga preferred, qxl for streaming only") is
  unmeasured for video workloads. Three test-session
  experiments: (a) Debian 13 + QXL to test whether a
  newer guest driver reduces OOM frequency; (b) Debian 11
  + virtio-vga to confirm/refute "no streaming, just
  bitmap blits"; (c) virtio-vga + accel3d='yes' to see
  if virgl changes the picture. Output: a "Guest driver
  decision matrix" section in
  `docs/libvirt-spice-recommendations.md` with measured
  numbers for each, plus operator-facing guidance for
  four common workload shapes. Investigation-only — no
  ryll code changes are expected to fall out directly.
  Whether to run urgently depends on phase 13A: if 13A
  shows the OOM/eviction mechanism is recoverable, phase
  16 becomes confirmation; if OOMs are an unavoidable
  side-effect of QXL's command-ring sizing, phase 16
  becomes the main path. Recommended planning effort:
  **medium** (the per-run work is small; the comparison
  framework needs care).

- **Phase 17 — Patched libspice-server for hypothesis
  validation.** Phase 13A's source-read identified
  `NUM_TRACE_ITEMS = 8` (`server/display-channel-private.h:23`)
  as the binding constraint on stream re-engagement under
  OOM pressure. The natural sanity check is to rebuild
  Debian's `libspice-server1` package with that constant
  bumped to 128 (next power of two above
  `RED_RELEASE_BUNCH_SIZE = 64`, so a single OOM cycle no
  longer fully overwrites the trace ring) and measure
  whether stream re-engagement actually improves. Three
  steps: 17A — automate the patched .deb build via a
  shell script in `ryll-test-sessions/bin/`; 17B —
  operator install on one hypervisor and re-run the 006a
  workload, capture comparable bundle (tag
  `007a-patched`), measure; 17C — file the upstream issue
  with the result, draft a phase 18 stub iff 17B is
  positive AND operator decides cluster-wide rollout is
  worth the maintenance tail. Gated on session 006
  confirming the trace-ring-contention model first — if
  006d (fullscreen 64 MiB) doesn't beat 006c (windowed
  256 MiB), the workload-driven command-ring is the real
  floor and `NUM_TRACE_ITEMS` alone won't help; this phase
  pivots or is cancelled. Recommended planning effort:
  **medium** (the package build is reasonable but
  unfamiliar; the test recipe is small).

- **Phase 18 — Push audit.** Work `PUSH-AUDIT.md` over the
  accumulated diff of every phase in this plan that has
  landed by the time the audit runs, not the last phase's
  diff alone — seventeen phases of capability, decoder,
  diagnostics and cache work have had plenty of opportunity
  to leave duplicated helpers and stale docs behind each
  other. Parked phases 13 / 16 / 17 and the awaiting-
  reproduction phase 15 do not gate it; if any of them
  later lands, it gets its own audit. Findings land as
  their own PR against `develop`, recorded here under an
  *Items deferred from the push audit* heading — the shape
  `PLAN-web-frontend.md` uses for its *Items deferred from
  the post-Phase-N pre-push audit* sections, minus the phase
  number, because this phase audits the whole plan rather
  than a range of it. This plan is not complete until each
  is fixed or declined in writing. If
  the audit finds nothing, record that here in one
  sentence. The audited phases have already merged, so
  `develop...HEAD` is empty on the audit branch and the
  accumulated diff has to be assembled from their merge
  commits; record each phase's as it lands in the `Merged`
  column of the table above, and reconstruct the ones that
  landed before this convention. *Two ways this runbook is
  invoked* in `PUSH-AUDIT.md` has the rest.
  Recommended planning effort: **low** (the runbook does the
  work; the phase plan is a wrapper).

## Agent guidance

### Read the source first

When a question about server / qemu / SPICE protocol behaviour
comes up — "why does the server do X?", "is the encoder
hardware-accelerated?", "what's the streaming heuristic?" —
**read the actual code before speculating**. The canonical
references live locally:

- `/srv/src-reference/spice/spice-protocol/` — wire format definitions, vd_agent.h, enums.h, message structs
- `/srv/src-reference/spice/spice/server/` — the spice-server implementation (display-channel.cpp, video-stream.cpp, mjpeg-encoder.c, gstreamer-encoder.c, image-encoders.cpp)
- `/srv/src-reference/spice/spice-gtk/` — the reference C client (for cross-checking how a known-good client interprets a given message)
- `/srv/src-reference/qemu/qemu/` — qemu, especially `ui/spice-*.c` for the server-side glue and `hw/display/qxl*` for the QXL device

Session 003a was an object lesson: I spent five sessions
inferring streaming heuristics from client-side counters
when the answer (`#define RED_STREAM_MIN_SIZE (96*96)`,
`SPICE_IMAGE_TYPE_BITMAP` requirement, `QXL_DRAW_COPY +
QXL_EFFECT_OPAQUE + SPICE_ROPD_OP_PUT`) was sitting in
`spice/server/display-channel.cpp:1057-1078` the whole
time. Two minutes of `grep` would have replaced two weeks
of guessing. **Default to reading the code; speculate only
when the code can't answer the question.**

Sub-agent briefs that touch protocol behaviour should
explicitly point at the relevant source paths under
`/srv/src-reference` so the agent's first move is grep,
not guess.

### Execution model

All implementation work is done by sub-agents, never
in the management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step
   with the brief from the plan, at the recommended
   effort level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or
   the model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This applies to all steps, including high-effort ones.
If a sub-agent can't succeed even with a detailed brief
and the right model, that's a signal the brief needs
improving, not that the management session should do
the implementation itself.

Use `isolation: "worktree"` for sub-agents when the
change is risky or experimental. The worktree is
discarded if the output is unsatisfactory. For safe,
well-understood changes, sub-agents can work directly
in the main tree.

### Planning effort

Phase plans should be created at the effort level recommended
in the phase summary above. Most of this plan's phases are
high or medium effort; phase 8 is low.

### Step-level guidance

Each phase plan should include a step table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

The model choice (opus / sonnet / haiku) should reflect the
quality of the brief and the complexity of the change. Briefs
that front-load research the planner already did allow lighter
models to succeed.

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code builds (`pre-commit run --all-files` or
      equivalent).
- [ ] Tests pass (`make test` for ryll).
- [ ] The changes match the intent of the brief — not
      just syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the Co-Authored-By line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented when
all of the following are true:

* `pre-commit run --all-files` is clean (rustfmt, clippy with
  `-D warnings`, shellcheck, secret/unicode scanners).
* `make test` passes; new logic has unit tests.
* `make build` and `make release` both succeed (verifies the
  H.264 dependency is correctly wired in the GUI binary, not
  just the WebRTC crate).
* ryll's display-channel capability advertisement includes,
  at minimum, the four new caps (`STREAM_REPORT` (4),
  `LZ4_COMPRESSION` (5), `MULTI_CODEC` (8), `CODEC_MJPEG` (9),
  `CODEC_H264` (11), `PREF_COMPRESSION` (6),
  `PREF_VIDEO_CODEC_TYPE` (12)) per the priority list above.
* `STREAM_ACTIVATE_REPORT` from the server triggers periodic
  `STREAM_REPORT` replies whose contents match spice-gtk's
  semantics. The reports are visible in `channel-state.json`
  (new per-stream `last_report_*` fields).
* `shakenfist-spice-compression::jpeg::best_for_platform()`
  selects ImageIO on macOS, WIC on Windows, VA-API (when
  available) or libjpeg-turbo on Linux. The active backend is
  visible in `channel-state.json::streams_active[*].mjpeg_decoder_backend`,
  and `mjpeg_decode_recent_mean_us` is well under the prior
  pure-Rust baseline on each platform (target ≤30 ms at
  2048×1152 on macOS Apple Silicon).
* The server can negotiate H.264 stream encoding with ryll;
  H.264 stream_data frames are decoded and painted with
  per-stream counters incrementing in line with frames_received.
* When the spice-server flap pattern (≥3 destroys / 30 s, mean
  lifetime < 3 s) is observed, a `NotifySeverity::Warn`
  notification fires once per 60 s cool-down and includes the
  observed counts.
* `MainSnapshot` carries vdagent reply-lag counters
  (`agent_request_count`, `agent_reply_count`,
  `last_agent_reply_lag_us`, `recent_agent_reply_lag_us`,
  `outstanding_agent_request_count`), populated whenever the
  guest agent is connected, and visible in `channel-state.json`.
* `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, and
  `docs/troubleshooting.md` reflect the new caps, the flap
  notification, and the vdagent probe.
* Lines wrapped at 120 chars; Rust strings use single quotes
  where applicable; trailing whitespace trimmed.

### Future work

Items deliberately deferred from this plan:

* **VP8 / VP9 / H.265 codec support.** Lower expected value
  than H.264 once that is in. Reconsider if the H.264 path is
  consistently chosen by the server but a workload (e.g. an
  H.265-only camera feed) shows up.
* **Stream-flap heuristic tuning.** Phase 6 starts with the
  ≥3-in-30 s rule; we may want to revisit constants once we
  have field experience.
* **Vdagent probe heuristic tuning.** Phase 7 starts with a
  30 s probe cadence and a 5 s outstanding-reply timeout; the
  right values depend on what we see in the field.
* **`GL_SCANOUT` cap.** Only useful if we add a zero-copy GL
  surface path. Not on the roadmap.

### Bugs fixed during this work

(Populated during execution.)

### Documentation index maintenance

When this master plan lands:

* **`docs/plans/index.md`** — add a row to the *Master plans*
  table with the creation date, link, intent summary, status
  (`In progress`), and links to each phase plan as they are
  written.
* **`docs/plans/order.yml`** — add an entry
  `- PLAN-stream-caps-and-flap.md: Stream caps and flap diagnostics`.

When all phases complete, flip the index row's status to
`Complete`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
