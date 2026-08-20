# Diagnosing "video stream not keeping up" reports

## Prompt

Build the instrumentation and (where warranted) the threading
changes needed so that future "video stream isn't keeping up"
bug reports are actionable — i.e. a developer reading the report
can tell whether the server stopped sending frames, the network
was slow, decode was slow, render was slow, or our own bug-report
I/O (pcap, screenshot encoding) was applying backpressure to the
SPICE socket.

This master plan was spun out of the session-001 dogfooding
triage (`PLAN-session-001-feedback.md`), which logged a
`U1: video stream appears to not be keeping up` report
(`ryll-bugreport-2026-05-05T10-39-47Z.zip`) without enough data
to act on. Rather than guess at a fix, we're closing the
observability gap first.

When working through phases, follow the project's plan
conventions (per-phase plan files named
`PLAN-video-keeping-up-phase-NN-*.md`, one logical change per
commit, master-plan table updated as work lands).

## Situation

Findings from a code walk of the display pipeline as of this
plan's creation:

- **Display channel pipeline is single-threaded** — socket read,
  message parse, image decode, and `ImageReady` emission all run
  on one async task
  (`shakenfist-spice-renderer/src/channels/display.rs:575`).
  Backlogs can accumulate at the socket buffer, the decode step
  (especially GLZ), or implicitly downstream of `ImageReady`.

- **Bug-report / capture I/O is inline on hot paths:**
  - `PcapChannelWriter` (`ryll/src/capture.rs:28`) is
    `Mutex`-guarded and writes to an unbuffered `File`. It is
    invoked on the display *read* task at `display.rs:631`
    (every received chunk) and on the *send* path at
    `display.rs:2170`. Slow disk on the read side back-pressures
    the SPICE socket directly.
  - `VideoWriter` (H.264 + MP4) at `ryll/src/capture.rs:230`
    encodes synchronously, but the call site is
    `CaptureSession::frame()` at `capture.rs:622`, invoked from
    the egui event handler at `app.rs:1615` in response to
    `ChannelEvent::ImageReady` — **not** on the display read
    task. So video encoding cannot directly stall the SPICE
    socket; it can, however, block the GUI event loop and starve
    presentation. Phase 3 addresses this UI-side backpressure
    rather than socket-side backpressure.

- **Existing metrics partially diagnose the problem:**
  - `last_latency_ms`: PING-to-PING interval on the main channel
    (`main_channel.rs:502`) — captures network + server-send
    delay, **not** client processing time.
  - `bandwidth_current` / `bandwidth_history`: socket arrival
    bytes/sec sampled once per second.
  - `fps`: derived from `DisplayMark` timestamps — measures
    presentation rate.
  - `frames_received`: cumulative `ImageReady` count.
  - `DisplaySnapshot.recent_decodes`: VecDeque (cap
    `MAX_RECENT_DECODES = 20`) with per-decode success flag,
    image type, dimensions, and session-relative timestamp —
    **no wall-clock duration**.

- **Gaps that block diagnosis today:**
  - No decode wall-time per image; can't tell GLZ is choking.
  - No socket-buffer high-water mark; can't tell the read loop is
    falling behind.
  - No ACK-window-exhaustion signal; can't tell we're applying
    backpressure at the SPICE level.
  - No render-side arrival-to-display latency; can't tell the
    renderer is the bottleneck once decode finishes.

## Mission and problem statement

Make a "video not keeping up" bug report self-diagnosing: the
report alone, without re-running the session, should tell a
maintainer which of {server, network, decode, render, our own
I/O backpressure} is the bottleneck. Where measurement reveals
ryll itself is the bottleneck, fix it.

## Approach

Instrumentation before threading. The cheap signals (phase 1)
will tell us whether the threading work in phases 2–3 is even
worth doing. If phase 1 reveals the bottleneck is consistently
decode CPU or server-side, we may stop after phase 1 + a fix in
the right place rather than refactoring I/O paths that aren't
actually hot.

## Resolved decisions

1. **Drop-on-overflow** for the future pcap and screenshot
   writer tasks (phases 2 and 3). Dropping preserves the socket
   read rate; the cost is gaps in pcaps when disk falls behind.
   Each dropped item increments a counter exposed in the
   snapshot (`writer_dropped_count` per channel) so a bug
   report makes the drop visible. Blocking would reintroduce
   the backpressure the threading split is meant to remove.

2. **Last-N plus min/max/mean** for decode duration, sharing
   the existing `recent_decodes` ring (cap
   `MAX_RECENT_DECODES = 20` in `display.rs`). Cheap, matches
   the surrounding code, and the aggregate stats give a
   long-run summary without the state cost of a full histogram.
   Computed at snapshot-emit time over the ring contents.

3. **Render-side latency = mpsc-queue lag** between event
   emission in the display channel and event drain in the
   egui frame loop. Captured as `produced_at_secs: f64` on
   `ChannelEvent::ImageReady*` and `ChannelEvent::DisplayMark`
   at emit; consumed in `process_events` to compute
   `consumed_at - produced_at` and feed two bounded rings
   surfaced as min/max/mean aggregates on `AppSnapshot`.
   Finer-grained "inside the egui paint" timing was rejected
   as disproportionate; end-to-end "pixels visible to user"
   is unmeasurable without external instrumentation. See
   `PLAN-video-keeping-up-phase-04-render-latency.md`.

4. **Asynchronous MP4 finalisation**, accepted as a
   deliberate regression when phase 3 moved encoding off the
   GUI event loop. `CaptureSession::close()` drops the frame
   sender and returns immediately; the encoder task drains
   its queue and writes the moov atom afterwards, on the
   tokio runtime. A bug report assembled within milliseconds
   of `close()` can therefore capture an unfinalised
   (unplayable) MP4, and an abrupt shutdown can tear the
   runtime down before the final `write_end()`. Finalising
   synchronously was rejected because it would have put the
   caller back to blocking on the encoder draining, which is
   exactly the stall the phase was removing. In practice the
   queue is a few frames deep so the window is small. See
   `PLAN-video-keeping-up-phase-03-video-encode-thread.md`
   for the full analysis of both scenarios.

## Acceptance criteria

A "video stream not keeping up" bug report is self-diagnosing
when its `channel-state.json` for the `display` channel lets a
maintainer answer all of the following without re-running the
session:

- **Decode load.** What was the per-decode wall time (min / max
  / mean over the recent window)? Were failures or cache misses
  spiking? (Phase 1.)
- **Socket read pressure.** Was the read loop consistently
  filling its 256 KB chunks (a signal that the OS recv buffer
  had bytes waiting when we read)? (Phase 1.)
- **SPICE-level backpressure.** How often did we fill the ACK
  window before sending an ACK, and how long were the gaps
  between ACKs? (Phase 1.)
- **Inline writer cost.** When pcap capture is enabled, are
  writer drops happening? (Phase 2.)
- **GUI loop encode cost.** When MP4 capture is enabled, is
  encoding stalling presentation? (Phase 3.)
- **Render path.** Optional: once the prior signals show
  decode + I/O are healthy, can we attribute remaining latency
  to the renderer? (Phase 4.)

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Decode duration + socket fill + ACK-window signals | [PLAN-video-keeping-up-phase-01-instrumentation.md](/components/ryll/plans/PLAN-video-keeping-up-phase-01-instrumentation/) | Done |
| 2. Move pcap writes to a dedicated writer task | [PLAN-video-keeping-up-phase-02-pcap-thread.md](/components/ryll/plans/PLAN-video-keeping-up-phase-02-pcap-thread/) | Done |
| 3. Move MP4 video encoding off the GUI event loop | [PLAN-video-keeping-up-phase-03-video-encode-thread.md](/components/ryll/plans/PLAN-video-keeping-up-phase-03-video-encode-thread/) | Done |
| 4. Render-side arrival-to-display latency | [PLAN-video-keeping-up-phase-04-render-latency.md](/components/ryll/plans/PLAN-video-keeping-up-phase-04-render-latency/) | Done |

**Phase 1** is the gate. It produces the data needed to triage
U1 and tells us whether phases 2–4 are warranted. Done when the
display `channel-state.json` in a bug report includes per-decode
wall time, socket-read fill stats, and ACK-send stats, and a
maintainer can read those fields without consulting code.

**Phase 2** moves `PcapChannelWriter` writes off the channel
read tasks onto a dedicated writer task with a bounded queue.
Done when `packet_received` and `packet_sent` are non-blocking
enqueues, and dropped items are counted in the snapshot.

**Phase 3** moves `VideoWriter::write_frame()` off the egui
event handler onto a dedicated encoder task. Done when
`CaptureSession::frame()` is a non-blocking enqueue and the egui
event loop is not stalled by H.264 encoding.

**Phase 4** is optional and only justified if phases 1–3 leave
a residual gap where the renderer is suspected. Adds
arrival-to-display latency at the renderer.

Phases 2 and 3 can run in either order once phase 1 data
confirms inline I/O is actually a meaningful contributor.

Out of scope: changes to the SPICE protocol layer, decode
algorithm changes (GLZ, Lz4), or renderer architecture changes.
