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

- **Bug-report I/O is inline on the channel read path:**
  - `PcapChannelWriter` is `Mutex`-guarded and writes to an
    unbuffered `File`; `packet_sent` / `packet_received` block
    the channel task
    (`ryll/src/capture.rs:27`, called from
    `display.rs:607` etc.).
  - `VideoWriter` (H.264 + MP4) encodes synchronously on the
    display read task on every MARK boundary
    (`ryll/src/capture.rs:567`).
  - Slow disk therefore back-pressures the SPICE socket today.

- **Existing metrics partially diagnose the problem:**
  - `last_latency_ms`: PING-to-PING interval on the main channel
    (`main_channel.rs:502`) — captures network + server-send
    delay, **not** client processing time.
  - `bandwidth_current` / `bandwidth_history`: socket arrival
    bytes/sec sampled once per second.
  - `fps`: derived from `DisplayMark` timestamps — measures
    presentation rate.
  - `frames_received`: cumulative `ImageReady` count.
  - `DisplaySnapshot.recent_decodes`: VecDeque (cap 145) with
    per-decode success flag, image type, dimensions, and
    session-relative timestamp — **no wall-clock duration**.

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

## Open questions

1. **Drop-on-overflow vs. block-on-overflow** for the future
   pcap and screenshot writer tasks. Dropping preserves the
   socket read rate but creates gaps in pcaps; blocking keeps
   pcaps complete but reintroduces the backpressure we're trying
   to eliminate. Resolve before phase 2. Lean drop with a
   counter exposed in the snapshot.

2. **Histogram vs. last-N** for decode duration. A bounded ring
   of last-N decode durations is cheaper and matches
   `recent_decodes`; a histogram gives long-run distribution at
   the cost of more state. Resolve before phase 1. Lean last-N
   matching the existing 145-cap, plus simple min/max/mean.

3. **Definition of "render-side latency"** for phase 4. Is it
   `ImageReady` → next presented `DisplayMark`, or something
   finer-grained inside the renderer? Resolve when phase 4
   starts; depends on whether the renderer can expose a
   timestamp at present-time.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Decode duration + socket high-water + ACK-window exhaustion | PLAN-video-keeping-up-phase-01-instrumentation.md | Not started |
| 2. Move pcap writes to a dedicated writer task | PLAN-video-keeping-up-phase-02-pcap-thread.md | Not started |
| 3. Move screenshot encoding off the display task | PLAN-video-keeping-up-phase-03-screenshot-thread.md | Not started |
| 4. Render-side arrival-to-display latency | PLAN-video-keeping-up-phase-04-render-latency.md | Not started |

Phase 1 is the gate: it both produces the data needed to triage
U1, and tells us whether phases 2–4 are warranted. Phases 2 and
3 can run in either order once phase 1 lands, but should not
start until phase 1 data confirms inline I/O is actually
causing measurable backpressure on the read path. Phase 4 is
optional and only justified if phases 1–3 leave a residual gap
where the renderer is suspected.

Out of scope: changes to the SPICE protocol layer, decode
algorithm changes (GLZ, Lz4), or renderer architecture changes.
